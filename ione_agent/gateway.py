from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import frappe
import requests


class GatewayError(RuntimeError):
	pass


@dataclass(frozen=True)
class GatewayConfig:
	url: str
	token: str


def get_gateway_config() -> GatewayConfig:
	url = str(frappe.conf.get("ione_agent_gateway_url") or "").strip().rstrip("/")
	token = str(frappe.conf.get("ione_agent_gateway_token") or "").strip()
	if not url or not token:
		raise GatewayError("I-ONE Agent 网关尚未配置，请联系系统管理员。")
	if not url.startswith(("http://", "https://")):
		raise GatewayError("I-ONE Agent 网关地址无效。")
	return GatewayConfig(url=url, token=token)


class GatewayClient:
	def __init__(self, config: GatewayConfig | None = None) -> None:
		self.config = config or get_gateway_config()

	def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
		headers = dict(kwargs.pop("headers", {}))
		headers["Authorization"] = f"Bearer {self.config.token}"
		headers["Accept"] = "application/json"
		try:
			response = requests.request(
				method,
				f"{self.config.url}{path}",
				headers=headers,
				timeout=(4, 15),
				**kwargs,
			)
		except requests.RequestException as exc:
			raise GatewayError(f"无法连接 I-ONE Agent 网关：{exc}") from exc

		try:
			payload = response.json()
		except ValueError as exc:
			raise GatewayError(f"I-ONE Agent 网关返回了无效响应（HTTP {response.status_code}）。") from exc

		if response.status_code >= 400:
			detail = payload.get("detail") if isinstance(payload, dict) else None
			raise GatewayError(str(detail or f"I-ONE Agent 网关请求失败（HTTP {response.status_code}）。"))
		return payload

	def health(self) -> dict[str, Any]:
		return self._request("GET", "/health")

	def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
		return self._request("POST", "/v1/runs", json=payload)

	def get_run(self, run_id: str) -> dict[str, Any]:
		return self._request("GET", f"/v1/runs/{run_id}")

	def stop_run(self, run_id: str) -> dict[str, Any]:
		return self._request("POST", f"/v1/runs/{run_id}/stop")
