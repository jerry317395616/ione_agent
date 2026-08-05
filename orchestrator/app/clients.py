from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.settings import Settings


def parse_json(text: str, default: Any) -> Any:
	text = (text or "").strip()
	for candidate in (text, *re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)):
		candidate = candidate.strip()
		try:
			return json.loads(candidate)
		except json.JSONDecodeError:
			start_candidates = [index for index in (candidate.find("{"), candidate.find("[")) if index >= 0]
			if not start_candidates:
				continue
			start = min(start_candidates)
			end = max(candidate.rfind("}"), candidate.rfind("]"))
			if end <= start:
				continue
			try:
				return json.loads(candidate[start : end + 1])
			except json.JSONDecodeError:
				continue
	return default


class QwenClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def chat(self, system: str, user: str, *, temperature: float = 0.1, timeout: float = 180) -> str:
		with httpx.Client(timeout=timeout) as client:
			response = client.post(
				self.settings.qwen_chat_url,
				headers={"Authorization": f"Bearer {self.settings.qwen_api_key}"},
				json={
					"model": self.settings.qwen_model,
					"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
					"temperature": temperature,
					"stream": False,
				},
			)
			response.raise_for_status()
			return str(response.json()["choices"][0]["message"]["content"])

	def json(self, system: str, user: str, default: Any) -> Any:
		return parse_json(self.chat(system, user), default)


class HermesClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def research(self, prompt: str) -> list[dict[str, Any]]:
		if not self.settings.hermes_api_key:
			raise RuntimeError("Hermes API key is not configured")
		with httpx.Client(timeout=600) as client:
			response = client.post(
				f"{self.settings.hermes_url}/v1/chat/completions",
				headers={"Authorization": f"Bearer {self.settings.hermes_api_key}"},
				json={
					"model": "hermes-agent",
					"messages": [{"role": "user", "content": prompt}],
					"stream": False,
				},
			)
			response.raise_for_status()
			content = str(response.json()["choices"][0]["message"]["content"])
		parsed = parse_json(content, [])
		if isinstance(parsed, dict):
			parsed = parsed.get("candidates") or parsed.get("results") or []
		return parsed if isinstance(parsed, list) else []


class SearxngClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	def search(self, queries: list[str], *, limit: int) -> list[dict[str, Any]]:
		results: list[dict[str, Any]] = []
		seen: set[str] = set()
		with httpx.Client(timeout=40, follow_redirects=True) as client:
			for query in queries[:8]:
				try:
					response = client.get(
						f"{self.settings.searxng_url}/search",
						params={"q": query, "format": "json", "language": "zh-CN"},
					)
					response.raise_for_status()
				except httpx.HTTPError:
					continue
				for item in response.json().get("results") or []:
					url = str(item.get("url") or "").strip()
					if not url or url in seen:
						continue
					seen.add(url)
					results.append(
						{
							"title": item.get("title"),
							"source_url": url,
							"source_name": item.get("engine") or "搜索引擎",
							"raw_text": item.get("content") or "",
							"evidence": [{"query": query, "snippet": item.get("content") or "", "url": url}],
						}
					)
					if len(results) >= limit:
						return results
		return results


class DeepSeekClient:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings

	@staticmethod
	def _extract(payload: Any) -> str:
		if isinstance(payload, str):
			return payload
		if isinstance(payload, dict):
			for key in ("answer", "result", "output", "content", "text", "message"):
				if key in payload:
					value = DeepSeekClient._extract(payload[key])
					if value:
						return value
		return ""

	def review(self, prompt: str) -> list[dict[str, Any]]:
		if not self.settings.deepseek_token:
			raise RuntimeError("DeepSeek review token is not configured")
		headers = {"Authorization": f"Bearer {self.settings.deepseek_token}"}
		with httpx.Client(timeout=60) as client:
			response = client.post(
				f"{self.settings.deepseek_url}/jobs",
				headers=headers,
				json={"prompt": prompt, "conversationKey": "ione-agent-lead-review"},
			)
			response.raise_for_status()
			job = response.json()
			job_id = job.get("id") or job.get("jobId") or job.get("job_id")
			if not job_id:
				content = self._extract(job)
				parsed = parse_json(content, [])
				return parsed if isinstance(parsed, list) else []
			deadline = time.monotonic() + 900
			while time.monotonic() < deadline:
				status = client.get(f"{self.settings.deepseek_url}/jobs/{job_id}", headers=headers)
				status.raise_for_status()
				payload = status.json()
				state = str(payload.get("status") or "").lower()
				if state in {"failed", "error", "cancelled"}:
					raise RuntimeError(self._extract(payload) or f"DeepSeek job {state}")
				if state in {"completed", "complete", "done", "succeeded", "success"}:
					parsed = parse_json(self._extract(payload), [])
					return parsed if isinstance(parsed, list) else []
				time.sleep(3)
		raise TimeoutError("DeepSeek review did not finish within 15 minutes")
