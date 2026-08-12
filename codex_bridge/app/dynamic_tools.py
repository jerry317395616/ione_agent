from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from app.identity import ToolIdentity, issue_actor_token
from app.settings import Settings

logger = logging.getLogger(__name__)


class DynamicToolProxy:
	"""Expose the permission-aware Frappe MCP tools through app-server dynamic tools.

	This transport avoids relying on the Codex MCP tool dispatcher while preserving the
	MCP endpoint as the sole execution boundary and source of tool schemas.
	"""

	def __init__(self, settings: Settings) -> None:
		self.settings = settings
		self._specs: list[dict[str, Any]] | None = None
		self._tool_names: set[str] = set()
		self._lock = asyncio.Lock()

	async def specs(self) -> list[dict[str, Any]]:
		if self._specs is not None:
			return self._specs
		async with self._lock:
			if self._specs is None:
				self._specs = await asyncio.to_thread(self._load_specs)
		return self._specs

	async def call(
		self,
		tool: str,
		arguments: dict[str, Any],
		*,
		identity: ToolIdentity | None,
	) -> dict[str, Any]:
		await self.specs()
		if tool not in self._tool_names:
			return self._failure("该业务工具未启用。")
		if identity is None or len(self.settings.identity_shared_secret) < 32:
			return self._failure("当前登录身份不完整，请重新进入 I-ONE Agent 后重试。")
		arguments = dict(arguments)
		arguments["actor_token"] = issue_actor_token(
			email=identity.email,
			user_hint=identity.user_hint,
			audience=identity.audience,
			secret=self.settings.identity_shared_secret,
		)
		try:
			response = await asyncio.to_thread(
				self._rpc,
				"tools/call",
				{"name": tool, "arguments": arguments},
			)
		except Exception:
			logger.exception("Frappe dynamic tool call failed tool=%s", tool)
			return self._failure("业务数据服务暂时不可用，请稍后重试。")

		content_items = []
		for item in response.get("content") or []:
			if isinstance(item, dict) and item.get("type") == "text":
				content_items.append({"type": "inputText", "text": str(item.get("text") or "")})
		if not content_items:
			content_items.append(
				{
					"type": "inputText",
					"text": json.dumps(
						response.get("structuredContent") or {}, ensure_ascii=False, default=str
					),
				}
			)
		return {"contentItems": content_items, "success": not bool(response.get("isError"))}

	def _load_specs(self) -> list[dict[str, Any]]:
		response = self._rpc("tools/list", {})
		allowed = set(self.settings.frappe_mcp_enabled_tools)
		specs = []
		for definition in response.get("tools") or []:
			if not isinstance(definition, dict):
				continue
			name = str(definition.get("name") or "")
			if not name or (allowed and name not in allowed):
				continue
			input_schema = definition.get("inputSchema") or {
				"type": "object",
				"properties": {},
			}
			if isinstance(input_schema, dict):
				input_schema = dict(input_schema)
				properties = input_schema.get("properties")
				if isinstance(properties, dict):
					properties = dict(properties)
					properties.pop("actor_token", None)
					input_schema["properties"] = properties
				required = input_schema.get("required")
				if isinstance(required, list):
					input_schema["required"] = [item for item in required if item != "actor_token"]
			specs.append(
				{
					"type": "function",
					"name": name,
					"description": str(definition.get("description") or ""),
					"inputSchema": input_schema,
				}
			)
		self._tool_names = {spec["name"] for spec in specs}
		if not specs:
			raise RuntimeError("Frappe MCP returned no enabled tools")
		return specs

	def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
		payload = json.dumps(
			{"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
			ensure_ascii=False,
		).encode("utf-8")
		headers = {
			"Accept": "application/json",
			"Authorization": self.settings.frappe_auth_header,
			"Content-Type": "application/json",
		}
		if self.settings.frappe_site_host:
			headers["Host"] = self.settings.frappe_site_host
		request = urllib.request.Request(
			self.settings.frappe_mcp_url,
			data=payload,
			headers=headers,
			method="POST",
		)
		try:
			with urllib.request.urlopen(request, timeout=60) as response:
				body = response.read()
		except (OSError, urllib.error.URLError) as exc:
			raise RuntimeError("Frappe MCP request failed") from exc
		try:
			message = json.loads(body)
		except (TypeError, ValueError) as exc:
			raise RuntimeError("Frappe MCP returned invalid JSON") from exc
		if not isinstance(message, dict):
			raise RuntimeError("Frappe MCP returned an invalid response")
		if message.get("error"):
			raise RuntimeError(str((message.get("error") or {}).get("message") or "MCP error"))
		result = message.get("result")
		if not isinstance(result, dict):
			raise RuntimeError("Frappe MCP returned no result")
		return result

	@staticmethod
	def _failure(message: str) -> dict[str, Any]:
		return {
			"contentItems": [{"type": "inputText", "text": message}],
			"success": False,
		}
