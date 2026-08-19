"""Server-side Dify integration for the I-ONE Agent workspace.

The browser never receives the Dify application key. Frappe authenticates the
human user, maps that user to a stable opaque Dify identity, and proxies the
stream from a published Dify chat application in a background worker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import frappe
import requests


class DifyError(RuntimeError):
	pass


@dataclass(frozen=True)
class DifyConfig:
	base_url: str
	api_key: str
	timeout: int = 300
	model_label: str = "Dify / Qwen"

	@classmethod
	def from_frappe_config(cls) -> DifyConfig:
		base_url = str(frappe.conf.get("ione_agent_dify_base_url") or "").strip().rstrip("/")
		api_key = str(frappe.conf.get("ione_agent_dify_api_key") or "").strip()
		timeout = int(frappe.conf.get("ione_agent_dify_timeout") or 300)
		model_label = str(frappe.conf.get("ione_agent_dify_model_label") or "Dify / Qwen").strip()
		parsed = urlparse(base_url)
		if parsed.scheme not in {"http", "https"} or not parsed.netloc:
			raise DifyError("Dify 服务地址尚未配置或无效。")
		if not api_key:
			raise DifyError("Dify 应用 API 密钥尚未配置。")
		return cls(base_url=base_url, api_key=api_key, timeout=max(30, timeout), model_label=model_label)


def stable_user_id(user: str) -> str:
	"""Return a stable, non-PII Dify end-user identifier for a Frappe user."""
	secret = str(
		frappe.conf.get("ione_agent_dify_user_secret")
		or frappe.conf.get("encryption_key")
		or ""
	).strip()
	if not secret:
		raise DifyError("Dify 用户映射密钥尚未配置。")
	site = str(getattr(frappe.local, "site", "") or "site")
	digest = hmac.new(secret.encode(), f"{site}:{user}".encode(), hashlib.sha256).hexdigest()
	return f"frappe-{digest[:40]}"


class DifyClient:
	def __init__(self, config: DifyConfig | None = None) -> None:
		self.config = config or DifyConfig.from_frappe_config()

	@property
	def headers(self) -> dict[str, str]:
		return {
			"Authorization": f"Bearer {self.config.api_key}",
			"Accept": "application/json",
			"Content-Type": "application/json",
		}

	def get_info(self) -> dict[str, Any]:
		try:
			response = requests.get(
				f"{self.config.base_url}/info",
				headers=self.headers,
				timeout=(4, 15),
			)
		except requests.RequestException as exc:
			raise DifyError(f"无法连接 Dify：{exc}") from exc
		return self._json_response(response)

	def stream_chat(
		self,
		*,
		query: str,
		user: str,
		conversation_id: str | None = None,
		inputs: dict[str, Any] | None = None,
	) -> Iterator[dict[str, Any]]:
		payload: dict[str, Any] = {
			"query": query,
			"inputs": inputs or {},
			"user": user,
			"response_mode": "streaming",
		}
		if conversation_id:
			payload["conversation_id"] = conversation_id
		try:
			with requests.post(
				f"{self.config.base_url}/chat-messages",
				headers=self.headers,
				json=payload,
				stream=True,
				timeout=(5, self.config.timeout),
			) as response:
				if response.status_code >= 400:
					raise DifyError(self._error_message(response))
				for raw_line in response.iter_lines(decode_unicode=True):
					line = (raw_line or "").strip()
					if not line.startswith("data: "):
						continue
					try:
						event = json.loads(line[6:])
					except json.JSONDecodeError as exc:
						raise DifyError("Dify 返回了无效的流式事件。") from exc
					if event.get("event") == "error":
						raise DifyError(str(event.get("message") or "Dify 工作流执行失败。"))
					yield event
		except DifyError:
			raise
		except requests.RequestException as exc:
			raise DifyError(f"Dify 流式连接中断：{exc}") from exc

	def stop_chat(self, task_id: str, user: str) -> dict[str, Any]:
		try:
			response = requests.post(
				f"{self.config.base_url}/chat-messages/{task_id}/stop",
				headers=self.headers,
				json={"user": user},
				timeout=(4, 20),
			)
		except requests.RequestException as exc:
			raise DifyError(f"无法停止 Dify 任务：{exc}") from exc
		return self._json_response(response)

	@staticmethod
	def _error_message(response: requests.Response) -> str:
		try:
			payload = response.json()
		except ValueError:
			payload = {}
		return str(
			payload.get("message")
			or payload.get("error")
			or f"Dify 请求失败（HTTP {response.status_code}）。"
		)

	@classmethod
	def _json_response(cls, response: requests.Response) -> dict[str, Any]:
		if response.status_code >= 400:
			raise DifyError(cls._error_message(response))
		try:
			payload = response.json()
		except ValueError as exc:
			raise DifyError(f"Dify 返回了无效响应（HTTP {response.status_code}）。") from exc
		if not isinstance(payload, dict):
			raise DifyError("Dify 返回的数据格式无效。")
		return payload
