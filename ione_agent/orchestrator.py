from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import frappe
import requests


class OrchestratorError(RuntimeError):
	pass


@dataclass(frozen=True)
class OrchestratorConfig:
	url: str
	token: str


def get_orchestrator_config() -> OrchestratorConfig:
	url = str(frappe.conf.get("ione_agent_orchestrator_url") or "").strip().rstrip("/")
	token = str(frappe.conf.get("ione_agent_orchestrator_token") or "").strip()
	if not url or not token:
		raise OrchestratorError("AI 获客编排服务尚未配置，请联系系统管理员。")
	if not url.startswith(("http://", "https://")):
		raise OrchestratorError("AI 获客编排服务地址无效。")
	return OrchestratorConfig(url=url, token=token)


class OrchestratorClient:
	def __init__(self, config: OrchestratorConfig | None = None) -> None:
		self.config = config or get_orchestrator_config()

	def _request(self, method: str, path: str, *, timeout: tuple[int, int] = (4, 20), **kwargs) -> Any:
		headers = dict(kwargs.pop("headers", {}))
		headers.update({"Authorization": f"Bearer {self.config.token}", "Accept": "application/json"})
		try:
			response = requests.request(
				method,
				f"{self.config.url}{path}",
				headers=headers,
				timeout=timeout,
				**kwargs,
			)
		except requests.RequestException as exc:
			raise OrchestratorError(f"无法连接 AI 获客编排服务：{exc}") from exc
		try:
			payload = response.json()
		except ValueError as exc:
			raise OrchestratorError(f"AI 获客编排服务返回了无效响应（HTTP {response.status_code}）。") from exc
		if response.status_code >= 400:
			detail = payload.get("detail") if isinstance(payload, dict) else None
			raise OrchestratorError(str(detail or f"AI 获客编排请求失败（HTTP {response.status_code}）。"))
		return payload

	def health(self) -> dict[str, Any]:
		return self._request("GET", "/health")

	def classify(self, message: str) -> str:
		payload = self._request("POST", "/v1/classify", json={"message": message}, timeout=(2, 8))
		return str(payload.get("intent") or "desktop")

	def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
		return self._request("POST", "/v1/runs", json=payload)

	def get_run(self, run_id: str) -> dict[str, Any]:
		return self._request("GET", f"/v1/runs/{run_id}")

	def stop_run(self, run_id: str) -> dict[str, Any]:
		return self._request("POST", f"/v1/runs/{run_id}/stop")
