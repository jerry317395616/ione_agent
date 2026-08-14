from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import shlex
import subprocess
import urllib.error
import urllib.request
from pathlib import Path, PurePath
from typing import Any

from app.identity import ToolIdentity, issue_actor_token
from app.settings import Settings

logger = logging.getLogger(__name__)

RAW_SPREADSHEET_READ_TOOL = "frappe_read_spreadsheet_attachment"
RAW_SPREADSHEET_WRITE_TOOL = "frappe_attach_spreadsheet_file"
STAGE_SPREADSHEET_TOOL = "frappe_stage_spreadsheet_attachment"
PUBLISH_SPREADSHEET_TOOL = "frappe_publish_spreadsheet_attachment"
OFFICECLI_XLSX_TOOL = "officecli_xlsx"
OFFICECLI_ALLOWED_VERBS = {
	"add",
	"batch",
	"close",
	"create",
	"dump",
	"get",
	"help",
	"import",
	"load_skill",
	"move",
	"open",
	"query",
	"remove",
	"save",
	"set",
	"swap",
	"validate",
	"view",
}
OFFICECLI_FILE_SUFFIXES = {".csv", ".json", ".tsv", ".xlsx"}


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
		workspace: str | Path | None = None,
	) -> dict[str, Any]:
		await self.specs()
		if tool not in self._tool_names:
			return self._failure("该业务工具未启用。")
		if identity is None or len(self.settings.identity_shared_secret) < 32:
			return self._failure("当前登录身份不完整，请重新进入 I-ONE Agent 后重试。")
		actor_token = issue_actor_token(
			email=identity.email,
			user_hint=identity.user_hint,
			audience=identity.audience,
			secret=self.settings.identity_shared_secret,
		)
		try:
			if tool == STAGE_SPREADSHEET_TOOL:
				return await asyncio.to_thread(
					self._stage_spreadsheet, arguments, actor_token, workspace, identity
				)
			if tool == PUBLISH_SPREADSHEET_TOOL:
				return await asyncio.to_thread(
					self._publish_spreadsheet, arguments, actor_token, workspace, identity
				)
			if tool == OFFICECLI_XLSX_TOOL:
				return await asyncio.to_thread(self._run_officecli, arguments, workspace, identity)
		except Exception as exc:
			logger.exception("Local spreadsheet tool failed tool=%s", tool)
			return self._failure(str(exc) or "电子表格处理失败。")

		arguments = dict(arguments)
		arguments["actor_token"] = actor_token
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
		available_names = {
			str(definition.get("name") or "")
			for definition in response.get("tools") or []
			if isinstance(definition, dict)
		}
		for definition in response.get("tools") or []:
			if not isinstance(definition, dict):
				continue
			name = str(definition.get("name") or "")
			if not name or (allowed and name not in allowed):
				continue
			if name in {RAW_SPREADSHEET_READ_TOOL, RAW_SPREADSHEET_WRITE_TOOL}:
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
		if RAW_SPREADSHEET_READ_TOOL in available_names and (
			not allowed or RAW_SPREADSHEET_READ_TOOL in allowed
		):
			specs.append(self._stage_spreadsheet_spec())
		if RAW_SPREADSHEET_WRITE_TOOL in available_names and (
			not allowed or RAW_SPREADSHEET_WRITE_TOOL in allowed
		):
			specs.append(self._publish_spreadsheet_spec())
		officecli_bin = getattr(self.settings, "officecli_bin", None)
		if officecli_bin and Path(officecli_bin).is_file():
			specs.append(self._officecli_spec())
		self._tool_names = {spec["name"] for spec in specs}
		if not specs:
			raise RuntimeError("Frappe MCP returned no enabled tools")
		return specs

	@staticmethod
	def _stage_spreadsheet_spec() -> dict[str, Any]:
		return {
			"type": "function",
			"name": STAGE_SPREADSHEET_TOOL,
			"description": (
				"把当前用户有权读取的 Frappe 文档 .xlsx 附件安全下载到本次站点工作目录。"
				"返回 local_path；文件内容不会进入模型上下文。"
			),
			"inputSchema": {
				"type": "object",
				"properties": {
					"doctype": {"type": "string"},
					"document_name": {"type": "string"},
					"file_name": {"type": "string"},
				},
				"required": ["doctype", "document_name", "file_name"],
				"additionalProperties": False,
			},
		}

	@staticmethod
	def _publish_spreadsheet_spec() -> dict[str, Any]:
		return {
			"type": "function",
			"name": PUBLISH_SPREADSHEET_TOOL,
			"description": (
				"把工作目录中已验证的 .xlsx 作为新的私有附件上传到当前用户可写的 Frappe 文档。"
				"默认保留原附件，返回可下载的 file_url。"
			),
			"inputSchema": {
				"type": "object",
				"properties": {
					"doctype": {"type": "string"},
					"document_name": {"type": "string"},
					"local_path": {
						"type": "string",
						"description": "stage 工具返回的工作目录相对路径",
					},
					"file_name": {
						"type": "string",
						"description": "可选的最终附件名，必须以 .xlsx 结尾",
					},
				},
				"required": ["doctype", "document_name", "local_path"],
				"additionalProperties": False,
			},
		}

	@staticmethod
	def _officecli_spec() -> dict[str, Any]:
		return {
			"type": "function",
			"name": OFFICECLI_XLSX_TOOL,
			"description": (
				"在受限工作目录内执行一条 OfficeCLI XLSX 命令。参数 command 不要包含 officecli 前缀。"
				"先用 view/outline 或 get 检查，再用 batch/add/set 修改，最后执行 validate。"
			),
			"inputSchema": {
				"type": "object",
				"properties": {
					"command": {
						"type": "string",
						"description": (
							"例如 view spreadsheets/report.xlsx outline；"
							"或 add spreadsheets/report.xlsx / --type sheet --prop name=分析"
						),
					}
				},
				"required": ["command"],
				"additionalProperties": False,
			},
		}

	def _stage_spreadsheet(
		self,
		arguments: dict[str, Any],
		actor_token: str,
		workspace: str | Path | None,
		identity: ToolIdentity,
	) -> dict[str, Any]:
		remote_arguments = {
			"doctype": str(arguments.get("doctype") or ""),
			"document_name": str(arguments.get("document_name") or ""),
			"file_name": str(arguments.get("file_name") or ""),
			"actor_token": actor_token,
		}
		response = self._rpc("tools/call", {"name": RAW_SPREADSHEET_READ_TOOL, "arguments": remote_arguments})
		payload = self._decode_structured(response)
		encoded = str(payload.pop("content_base64", "") or "")
		try:
			content = base64.b64decode(encoded, validate=True)
		except (binascii.Error, ValueError) as exc:
			raise RuntimeError("站点返回的电子表格内容无效。") from exc
		if not content or len(content) > 8 * 1024 * 1024:
			raise RuntimeError("电子表格为空或超过 8 MB 限制。")
		name = PurePath(str(payload.get("file_name") or remote_arguments["file_name"])).name
		if not name.lower().endswith(".xlsx"):
			raise RuntimeError("附件不是受支持的 .xlsx 文件。")
		root = self._workspace_root(workspace, identity)
		target = (root / "spreadsheets" / name).resolve()
		self._ensure_inside_workspace(root, target)
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_bytes(content)
		target.chmod(0o600)
		payload["local_path"] = target.relative_to(root).as_posix()
		payload["file_size"] = len(content)
		return self._success(payload)

	def _publish_spreadsheet(
		self,
		arguments: dict[str, Any],
		actor_token: str,
		workspace: str | Path | None,
		identity: ToolIdentity,
	) -> dict[str, Any]:
		root = self._workspace_root(workspace, identity)
		local_path = self._workspace_file(root, str(arguments.get("local_path") or ""), {".xlsx"})
		content = local_path.read_bytes()
		if not content or len(content) > 8 * 1024 * 1024:
			raise RuntimeError("电子表格为空或超过 8 MB 限制。")
		file_name = PurePath(str(arguments.get("file_name") or local_path.name)).name
		if not file_name.lower().endswith(".xlsx"):
			raise RuntimeError("最终附件名必须以 .xlsx 结尾。")
		remote_arguments = {
			"doctype": str(arguments.get("doctype") or ""),
			"document_name": str(arguments.get("document_name") or ""),
			"file_name": file_name,
			"content_base64": base64.b64encode(content).decode("ascii"),
			"actor_token": actor_token,
		}
		response = self._rpc(
			"tools/call", {"name": RAW_SPREADSHEET_WRITE_TOOL, "arguments": remote_arguments}
		)
		return self._success(self._decode_structured(response))

	def _run_officecli(
		self, arguments: dict[str, Any], workspace: str | Path | None, identity: ToolIdentity
	) -> dict[str, Any]:
		command = str(arguments.get("command") or "").strip()
		if not command or len(command) > 50000:
			raise RuntimeError("OfficeCLI 命令为空或过长。")
		try:
			parts = shlex.split(command, posix=True)
		except ValueError as exc:
			raise RuntimeError("OfficeCLI 命令引号格式无效。") from exc
		if not parts or parts[0] not in OFFICECLI_ALLOWED_VERBS:
			raise RuntimeError("该 OfficeCLI 操作未开放。")
		root = self._workspace_root(workspace, identity)
		if not Path(self.settings.officecli_bin).is_file():
			raise RuntimeError("电子表格处理组件尚未安装。")
		rewritten = [parts[0]]
		xlsx_count = 0
		for token in parts[1:]:
			suffix = PurePath(token).suffix.lower()
			if suffix in OFFICECLI_FILE_SUFFIXES:
				path = self._workspace_file(root, token, OFFICECLI_FILE_SUFFIXES, must_exist=False)
				rewritten.append(str(path))
				xlsx_count += int(suffix == ".xlsx")
			else:
				rewritten.append(token)
		if parts[0] not in {"help", "load_skill"} and not xlsx_count:
			raise RuntimeError("OfficeCLI XLSX 操作必须指定工作目录内的 .xlsx 文件。")
		if parts[0] == "load_skill" and rewritten[1:] != ["officecli-xlsx"]:
			raise RuntimeError("这里只允许加载 officecli-xlsx Skill。")
		completed = subprocess.run(
			[str(self.settings.officecli_bin), *rewritten],
			cwd=root,
			capture_output=True,
			text=True,
			timeout=90,
			check=False,
		)
		output = (completed.stdout + ("\n" + completed.stderr if completed.stderr else "")).strip()
		output = output.replace(str(root), ".")
		if len(output) > 18000:
			output = output[:18000] + "\n…输出已截断"
		return self._success(
			{"exit_code": completed.returncode, "output": output or "命令已完成。"},
			success=completed.returncode == 0,
		)

	def _workspace_root(self, workspace: str | Path | None, identity: ToolIdentity) -> Path:
		if workspace is None:
			raise RuntimeError("当前会话没有可用的工作目录。")
		workspace_root = Path(workspace).resolve()
		configured = self.settings.workspace_root.resolve()
		if workspace_root != configured and configured not in workspace_root.parents:
			raise RuntimeError("当前工作目录超出允许范围。")
		actor_key = hashlib.sha256(identity.email.strip().lower().encode("utf-8")).hexdigest()[:24]
		root = (workspace_root / ".ione" / "spreadsheet-users" / actor_key).resolve()
		self._ensure_inside_workspace(workspace_root, root)
		root.mkdir(parents=True, exist_ok=True)
		return root

	@classmethod
	def _workspace_file(
		cls,
		root: Path,
		value: str,
		allowed_suffixes: set[str],
		*,
		must_exist: bool = True,
	) -> Path:
		if not value or Path(value).is_absolute():
			raise RuntimeError("文件路径必须是工作目录相对路径。")
		candidate = (root / value).resolve()
		cls._ensure_inside_workspace(root, candidate)
		if candidate.suffix.lower() not in allowed_suffixes:
			raise RuntimeError("文件类型不受支持。")
		if must_exist and not candidate.is_file():
			raise RuntimeError("工作目录中找不到指定文件。")
		candidate.parent.mkdir(parents=True, exist_ok=True)
		return candidate

	@staticmethod
	def _ensure_inside_workspace(root: Path, candidate: Path) -> None:
		if candidate != root and root not in candidate.parents:
			raise RuntimeError("文件路径超出工作目录。")

	@staticmethod
	def _decode_structured(response: dict[str, Any]) -> dict[str, Any]:
		value: Any = response.get("structuredContent")
		if not isinstance(value, dict) or not value:
			for item in response.get("content") or []:
				if not isinstance(item, dict) or item.get("type") != "text":
					continue
				try:
					value = json.loads(str(item.get("text") or ""))
				except ValueError:
					continue
				if isinstance(value, dict):
					break
		if isinstance(value, dict) and isinstance(value.get("result"), dict):
			value = value["result"]
		if not isinstance(value, dict):
			raise RuntimeError("业务工具未返回有效结果。")
		return dict(value)

	@staticmethod
	def _success(payload: dict[str, Any], *, success: bool = True) -> dict[str, Any]:
		return {
			"contentItems": [
				{"type": "inputText", "text": json.dumps(payload, ensure_ascii=False, default=str)}
			],
			"success": success,
		}

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
