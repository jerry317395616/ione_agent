from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class OracleBrowserError(RuntimeError):
	pass


@dataclass(frozen=True)
class OracleBrowserResult:
	reply: str
	conversation_url: str = ""


class OracleBrowserClient:
	"""Small async client for the loopback-only Oracle DeepSeek browser service."""

	def __init__(self, *, base_url: str, token: str, timeout_seconds: int) -> None:
		self.base_url = base_url.rstrip("/")
		self.token = token
		self.timeout_seconds = timeout_seconds

	async def ask(self, *, prompt: str, conversation_key: str) -> OracleBrowserResult:
		return await asyncio.to_thread(
			self._ask,
			prompt=prompt,
			conversation_key=conversation_key,
		)

	def _ask(self, *, prompt: str, conversation_key: str) -> OracleBrowserResult:
		created = self._request(
			"POST",
			"/jobs",
			{"prompt": prompt, "conversationKey": conversation_key},
		)
		job = created.get("job") or {}
		job_id = str(job.get("id") or "")
		if not job_id:
			raise OracleBrowserError("Oracle browser service did not return a job id")

		deadline = time.monotonic() + self.timeout_seconds
		while time.monotonic() < deadline:
			job = (self._request("GET", f"/jobs/{job_id}").get("job") or {})
			status = str(job.get("status") or "")
			if status == "completed":
				reply = str(job.get("reply") or "").strip()
				if not reply:
					raise OracleBrowserError("Oracle browser service returned an empty reply")
				return OracleBrowserResult(
					reply=reply,
					conversation_url=str(job.get("conversationUrl") or ""),
				)
			if status in {"failed", "cancelled"}:
				raise OracleBrowserError(str(job.get("error") or f"Oracle job {status}"))
			time.sleep(0.75)
		raise OracleBrowserError("Oracle browser request timed out")

	def _request(
		self,
		method: str,
		path: str,
		payload: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		data = None
		if payload is not None:
			data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
		request = urllib.request.Request(
			f"{self.base_url}{path}",
			data=data,
			headers={
				"Accept": "application/json",
				"Authorization": f"Bearer {self.token}",
				"Content-Type": "application/json; charset=utf-8",
			},
			method=method,
		)
		try:
			with urllib.request.urlopen(request, timeout=15) as response:
				body = response.read()
		except (OSError, urllib.error.URLError) as exc:
			raise OracleBrowserError("Oracle browser service is unavailable") from exc
		try:
			message = json.loads(body)
		except (TypeError, ValueError) as exc:
			raise OracleBrowserError("Oracle browser service returned invalid JSON") from exc
		if not isinstance(message, dict) or message.get("ok") is False:
			raise OracleBrowserError(str(message.get("error") or "Oracle browser request failed"))
		return message


def parse_oracle_action(text: str) -> dict[str, Any]:
	"""Parse a JSON action while tolerating a fenced browser response."""

	value = text.strip()
	if value.startswith("```"):
		lines = value.splitlines()
		if lines and lines[0].startswith("```"):
			lines = lines[1:]
		if lines and lines[-1].strip() == "```":
			lines = lines[:-1]
		value = "\n".join(lines).strip()
	try:
		parsed = json.loads(value)
		if isinstance(parsed, dict):
			return parsed
	except ValueError:
		pass

	start = value.find("{")
	end = value.rfind("}")
	if start >= 0 and end > start:
		try:
			parsed = json.loads(value[start : end + 1])
			if isinstance(parsed, dict):
				return parsed
		except ValueError:
			pass
	return {"action": "reply", "content": value}
