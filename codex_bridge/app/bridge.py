from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.app_server import AppServerError, CodexAppServer
from app.identity import tool_identity, with_trusted_identity_context
from app.oracle_browser import OracleBrowserClient, OracleBrowserError, parse_oracle_action
from app.public_output import public_error_message, sanitize_public_text
from app.recipe_import import (
	decode_tool_result,
	has_recipe_attachment,
	parse_recipe_attachment,
	preview_text,
	result_payload,
	wants_recipe_commit,
	wants_recipe_preview,
)
from app.settings import Settings
from app.store import ConversationStore

logger = logging.getLogger(__name__)


_SUMMARY_LIMIT = 1600
_PRIVATE_CONTEXT_PATTERN = re.compile(
	r"<ione_trusted_session>.*?</ione_trusted_session>", re.IGNORECASE | re.DOTALL
)
_ABSOLUTE_PATH_PATTERN = re.compile(
	r"(?<![\w:])(?:[A-Za-z]:[\\/]|/(?:home|root|opt|var|etc|srv|tmp)/)[^\s`'\"<>]+",
	re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s`'\"<>]+", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
	r"\b(?:authorization|api[_-]?key|api[_-]?secret|access[_-]?token|actor[_-]?token|token|password|secret)\s*[:=]\s*\S+",
	re.IGNORECASE,
)

_TOOL_LABELS = {
	"frappe_get_context": "读取站点上下文",
	"frappe_get_site_catalog": "读取站点功能目录",
	"frappe_search_doctypes": "查找业务功能",
	"frappe_get_doctype_meta": "读取业务字段结构",
	"frappe_list_documents": "查询业务数据",
	"frappe_get_document": "读取业务记录",
	"frappe_create_document": "新增业务记录",
	"frappe_update_document": "更新业务记录",
	"frappe_upsert_tongjianyun_recipe": "保存童健云完整食谱",
	"frappe_generate_tongjianyun_recipe_analysis": "生成食谱带量分析报告",
	"frappe_list_attachments": "查询业务附件",
	"frappe_attach_text_file": "保存文本附件",
	"frappe_attach_word_file": "保存文档附件",
	"frappe_read_word_attachment": "读取文档附件",
	"frappe_create_crm_lead_package": "创建客户业务资料",
	"frappe_convert_lead_to_deal": "转换客户商机",
	"frappe_upsert_deal_presentation": "生成客户演示文稿",
	"frappe_upsert_deal_video": "生成客户宣传视频",
	"frappe_submit_deal_video_render": "提交视频生成任务",
	"frappe_get_deal_video_render_status": "查询视频生成进度",
	"frappe_get_deal_video_sources": "读取视频素材",
}


def sanitize_reasoning_summary(value: object) -> str:
	"""Return a bounded, user-safe summary without private runtime details."""

	text = _PRIVATE_CONTEXT_PATTERN.sub("", str(value or ""))
	text = _SENSITIVE_ASSIGNMENT_PATTERN.sub("[安全信息]", text)
	text = _ABSOLUTE_PATH_PATTERN.sub("[内部路径]", text)
	text = _URL_PATTERN.sub("[链接]", text)
	text = sanitize_public_text(text).replace("<!-- -->", "").strip()
	if len(text) > _SUMMARY_LIMIT:
		text = f"{text[:_SUMMARY_LIMIT].rstrip()}…"
	return text


def _reasoning_item_text(item: dict[str, Any], fallback: str = "") -> str:
	parts: list[str] = []
	summary = item.get("summary") or []
	if isinstance(summary, str):
		parts.append(summary)
	elif isinstance(summary, list):
		for part in summary:
			if isinstance(part, str):
				parts.append(part)
			elif isinstance(part, dict):
				parts.append(str(part.get("text") or part.get("summary") or ""))
	return "\n\n".join(filter(None, parts)).strip() or fallback.strip()


def _tool_label(tool: object) -> str:
	name = str(tool or "").strip()
	if name in _TOOL_LABELS:
		return _TOOL_LABELS[name]
	if name.startswith("frappe_"):
		return "处理站点业务数据"
	return "调用业务工具"


def _item_label(item: dict[str, Any]) -> str | None:
	item_type = str(item.get("type") or "")
	if item_type == "commandExecution":
		return "执行受控系统操作"
	if item_type == "fileChange":
		return "更新工作区文件"
	if item_type == "mcpToolCall":
		return _tool_label(item.get("tool"))
	if item_type == "collabToolCall":
		return "协调后台任务"
	if item_type == "webSearch":
		return "检索公开资料"
	if item_type == "imageGeneration":
		return "生成图像"
	if item_type == "plan":
		return "制定执行计划"
	return None


def _duration_text(duration_ms: object) -> str:
	try:
		milliseconds = max(0.0, float(duration_ms))
	except (TypeError, ValueError):
		return ""
	if milliseconds < 1000:
		return "（少于 1 秒）"
	return f"（{milliseconds / 1000:.1f} 秒）"


@dataclass
class ProcessDisplay:
	"""Convert app-server lifecycle events into a safe, auditable display."""

	reasoning_buffers: defaultdict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
	seen_summaries: set[str] = field(default_factory=set)
	activity_labels: list[str] = field(default_factory=list)

	@staticmethod
	def initial() -> str:
		return "### 处理过程\n\n- 已收到请求，正在分析。\n"

	def _record_activity(self, label: str) -> None:
		if label not in self.activity_labels:
			self.activity_labels.append(label)

	def _fallback_summary(self) -> str:
		if not self.activity_labels:
			return "已确认请求目标，并完成必要分析。"
		activities = "、".join(self.activity_labels[:4])
		if len(self.activity_labels) > 4:
			activities += "等步骤"
		return f"已根据请求完成分析，并执行了{activities}。"

	def consume(self, event: dict[str, Any]) -> list[str]:
		method = str(event.get("method") or "")
		params = event.get("params") or {}
		if method == "item/reasoning/summaryTextDelta":
			item_id = str(params.get("itemId") or "reasoning")
			self.reasoning_buffers[item_id].append(str(params.get("delta") or ""))
			return []

		if method in {"bridge/dynamicTool/started", "bridge/dynamicTool/completed"}:
			label = _tool_label(params.get("tool"))
			if method.endswith("/started"):
				self._record_activity(label)
				return [f"- 正在{label}。\n"]
			status = "完成" if params.get("success", True) else "未成功"
			return [f"- {label}{status}{_duration_text(params.get('durationMs'))}。\n"]

		if method not in {"item/started", "item/completed", "turn/completed"}:
			return []
		if method == "turn/completed":
			turn = params.get("turn") or {}
			if str(turn.get("status") or "completed") == "completed":
				deltas: list[str] = []
				if not self.seen_summaries:
					deltas.append(f"\n**思考摘要**\n\n{self._fallback_summary()}\n\n")
				deltas.append("- 处理完成，正在整理结果。\n")
				return deltas
			return ["- 处理未成功，正在整理错误信息。\n"]

		item = params.get("item") or {}
		if not isinstance(item, dict):
			return []
		item_type = str(item.get("type") or "")
		if method == "item/completed" and item_type == "reasoning":
			item_id = str(item.get("id") or params.get("itemId") or "reasoning")
			fallback = "".join(self.reasoning_buffers.pop(item_id, []))
			summary = sanitize_reasoning_summary(_reasoning_item_text(item, fallback))
			if not summary or summary in self.seen_summaries:
				return []
			self.seen_summaries.add(summary)
			return [f"\n**思考摘要**\n\n{summary}\n\n"]

		label = _item_label(item)
		if not label:
			return []
		if method == "item/started":
			self._record_activity(label)
			return [f"- 正在{label}。\n"]
		self._record_activity(label)
		status = str(item.get("status") or "completed")
		result = "完成" if status == "completed" else "未成功"
		return [f"- {label}{result}{_duration_text(item.get('durationMs'))}。\n"]


@dataclass
class AgentMessageCollector:
	"""Keep interim commentary separate from the turn's final answer."""

	buffers: dict[str, list[str]] = field(default_factory=dict)
	item_order: list[str] = field(default_factory=list)
	phases: dict[str, str] = field(default_factory=dict)
	final_messages: list[str] = field(default_factory=list)
	legacy_messages: list[str] = field(default_factory=list)
	unscoped_deltas: list[str] = field(default_factory=list)

	@staticmethod
	def _phase(value: object) -> str:
		return str(value or "").strip().lower().replace("-", "_")

	def _ensure_item(self, item_id: str) -> list[str]:
		if item_id not in self.buffers:
			self.buffers[item_id] = []
			self.item_order.append(item_id)
		return self.buffers[item_id]

	def consume(self, event: dict[str, Any]) -> None:
		method = str(event.get("method") or "")
		params = event.get("params") or {}
		if method == "item/agentMessage/delta":
			delta = str(params.get("delta") or "")
			item_id = str(params.get("itemId") or "").strip()
			if item_id:
				self._ensure_item(item_id).append(delta)
				phase = self._phase(params.get("phase"))
				if phase:
					self.phases[item_id] = phase
			else:
				self.unscoped_deltas.append(delta)
			return

		if method not in {"item/started", "item/completed"}:
			return
		item = params.get("item") or {}
		if not isinstance(item, dict) or item.get("type") != "agentMessage":
			return
		item_id = str(item.get("id") or params.get("itemId") or "").strip()
		phase = self._phase(item.get("phase") or params.get("phase"))
		if method == "item/started":
			if item_id:
				self._ensure_item(item_id)
				if phase:
					self.phases[item_id] = phase
			return

		if item_id:
			phase = phase or self.phases.pop(item_id, "")
			buffered = "".join(self.buffers.pop(item_id, []))
		else:
			buffered = ""
		text = str(item.get("text") or buffered).strip()
		if not text:
			return
		if phase in {"final", "final_answer", "finalanswer"}:
			self.final_messages.append(text)
		elif phase not in {"commentary", "analysis"}:
			# Older app-server versions did not expose phase. The last completed
			# message remains the safest backward-compatible final-answer fallback.
			self.legacy_messages.append(text)

	def answer(self) -> str:
		if self.final_messages:
			return self.final_messages[-1].strip()
		if self.legacy_messages:
			return self.legacy_messages[-1].strip()
		# A delta-only legacy stream cannot expose phases. Return only the last
		# scoped message instead of concatenating every assistant message.
		for item_id in reversed(self.item_order):
			if text := "".join(self.buffers.get(item_id, [])).strip():
				return text
		return "".join(self.unscoped_deltas).strip()


class ChatMessage(BaseModel):
	model_config = ConfigDict(extra="ignore")

	role: str
	content: Any = ""


class ChatCompletionRequest(BaseModel):
	model_config = ConfigDict(extra="ignore")

	model: str = "ione-agent"
	messages: list[ChatMessage] = Field(min_length=1)
	stream: bool = False


def message_text(message: ChatMessage) -> str:
	if isinstance(message.content, str):
		return message.content.strip()
	if isinstance(message.content, list):
		parts = []
		for item in message.content:
			if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
				parts.append(str(item.get("text") or ""))
		return "\n".join(filter(None, parts)).strip()
	return str(message.content or "").strip()


def latest_user_text(request: ChatCompletionRequest) -> str:
	for message in reversed(request.messages):
		if message.role == "user" and (text := message_text(message)):
			return text
	return ""


def _compact_text(value: str) -> str:
	return re.sub(r"\s+", "", value or "")


def completion_response(model: str, answer: str) -> dict[str, Any]:
	return {
		"id": f"chatcmpl-{uuid.uuid4().hex}",
		"object": "chat.completion",
		"created": int(time.time()),
		"model": model,
		"choices": [
			{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}
		],
		"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
	}


def stream_chunk(
	completion_id: str,
	model: str,
	delta: dict[str, str],
	*,
	finish_reason: str | None = None,
) -> str:
	payload = {
		"id": completion_id,
		"object": "chat.completion.chunk",
		"created": int(time.time()),
		"model": model,
		"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
	}
	return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class CodexBridge:
	def __init__(self, settings: Settings, app_server: CodexAppServer) -> None:
		self.settings = settings
		self.app_server = app_server
		self.store = ConversationStore(settings.data_dir / "conversations.sqlite3")
		self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
		self.oracle_browser = (
			OracleBrowserClient(
				base_url=getattr(settings, "oracle_browser_url", "http://127.0.0.1:9474"),
				token=getattr(settings, "oracle_browser_token", ""),
				timeout_seconds=getattr(settings, "oracle_browser_timeout_seconds", 180),
			)
			if getattr(settings, "oracle_browser_enabled", False)
			else None
		)

	def close(self) -> None:
		self.store.close()

	def workspace_for(self, user_id: str) -> Path:
		if self.settings.workspace_scope == "site":
			workspace = self.settings.workspace_root
		else:
			digest = hashlib.sha256(user_id.encode()).hexdigest()[:24]
			workspace = (self.settings.workspace_root / digest).resolve()
			if self.settings.workspace_root not in workspace.parents:
				raise RuntimeError("Invalid workspace path")
		workspace.mkdir(parents=True, exist_ok=True)
		workspace.chmod(0o700)
		return workspace

	def _oracle_conversation_key(self, user_id: str, conversation_id: str) -> str:
		digest = hashlib.sha256(f"{user_id}\0{conversation_id}".encode()).hexdigest()[:40]
		return f"child-{digest}"

	async def _recipe_import_answer(
		self,
		text: str,
		*,
		user_id: str,
		conversation_id: str,
		manager_user_email: str | None,
		manager_user_hint: str | None,
	) -> str | None:
		"""Handle recipe uploads deterministically before invoking a language model."""

		if has_recipe_attachment(text):
			try:
				draft = parse_recipe_attachment(text).as_dict()
			except ValueError as exc:
				return f"食谱文件无法可靠解析：{exc}。未写入任何数据，请检查表头、日期和餐次后重新上传。"
			self.store.save_recipe_import(user_id, conversation_id, draft)
			return preview_text(draft)

		if not (wants_recipe_commit(text) or wants_recipe_preview(text)):
			return None
		if not hasattr(self, "store"):
			return None
		draft = self.store.latest_recipe_import(user_id, conversation_id)
		if not draft:
			return "当前对话没有待处理的食谱附件。请先上传 Excel 或 CSV 食谱文件。"
		if wants_recipe_preview(text) and not wants_recipe_commit(text):
			return preview_text(draft)
		if draft.get("status") == "committed":
			result = draft.get("commit_result") or {}
			report = result.get("analysis_report") or {}
			link = (
				f"\n\n[下载食谱带量分析报告]({report.get('download_url')})"
				if report.get("download_url")
				else ""
			)
			return f"该食谱已经录入童健云，记录编号为 {result.get('name') or result.get('recipe_id') or '—'}。{link}"
		stats = draft.get("stats") or {}
		if int(stats.get("error_count") or 0):
			return f"食谱导入任务 {draft.get('task_id')} 存在阻断错误，暂不能写入。\n\n{preview_text(draft)}"
		if int(stats.get("warning_count") or 0) and "确认" not in _compact_text(text):
			return (
				f"食谱导入任务 {draft.get('task_id')} 有 {stats.get('warning_count')} 条关系提示。"
				"为避免把食材写到错误菜品，请先核对预览，再回复“确认按当前解析结果录入食谱”。"
			)

		proxy = self.app_server.dynamic_tool_proxy
		if not proxy:
			return "童健云食谱写入服务暂时不可用，解析结果已经保存，可以稍后继续录入。"
		identity = tool_identity(
			email=manager_user_email,
			user_hint=manager_user_hint,
			mcp_url=self.settings.frappe_mcp_url,
			audience=self.settings.identity_audience,
			site_host=self.settings.frappe_site_host,
		)
		if identity is None:
			return "当前登录身份无法验证。解析结果已经保存，请重新进入 AI 员工后回复“录入食谱”。"
		payload = result_payload(draft)
		result = await proxy.call(
			"frappe_upsert_tongjianyun_recipe",
			payload,
			identity=identity,
		)
		decoded = decode_tool_result(result)
		if not result.get("success") or not decoded:
			self.store.finish_recipe_import(
				str(draft["task_id"]), status="failed", result=decoded or result
			)
			return "食谱解析结果已保留，但本次写入失败。没有返回可核验的童健云记录，请稍后重试。"
		expected = draft.get("stats") or {}
		mismatches = []
		for count_field in ("day_count", "dish_count", "ingredient_count"):
			if int(decoded.get(count_field) or -1) != int(expected.get(count_field) or 0):
				mismatches.append(count_field)
		if mismatches:
			self.store.finish_recipe_import(
				str(draft["task_id"]), status="failed", result=decoded
			)
			return "童健云写入后的回读数量与解析预览不一致，系统已标记为失败，请管理员检查数据。"
		report = {}
		try:
			report_result = await proxy.call(
				"frappe_generate_tongjianyun_recipe_analysis",
				{"recipe_name": decoded.get("name") or decoded.get("recipe_id")},
				identity=identity,
			)
			if report_result.get("success"):
				report = decode_tool_result(report_result)
		except Exception:
			logger.exception("Recipe analysis workbook generation failed after a verified save")
		commit_result = {**decoded, "analysis_report": report}
		self.store.finish_recipe_import(str(draft["task_id"]), status="committed", result=commit_result)
		report_lines = ""
		if report.get("download_url"):
			report_lines = (
				f"\n- 分析标准：{(report.get('analysis') or {}).get('profile') or '学龄前儿童默认档案'}"
				f"\n- [下载食谱带量分析报告]({report.get('download_url')})"
			)
		else:
			report_lines = "\n- 食谱已保存；分析报告暂未生成，可稍后回复“生成食谱分析报告”重试。"
		return (
			f"食谱已准确录入童健云。\n\n"
			f"- 食谱：{decoded.get('title') or (draft.get('recipe') or {}).get('title')}\n"
			f"- 记录编号：{decoded.get('name') or decoded.get('recipe_id')}\n"
			f"- 日期：{decoded.get('week_start') or expected.get('week_start')} 至 "
			f"{decoded.get('week_end') or expected.get('week_end')}\n"
			f"- 已核验：{decoded.get('day_count')} 天、{decoded.get('dish_count')} 道菜、"
			f"{decoded.get('ingredient_count')} 条食材明细"
			f"{report_lines}"
		)

	async def _oracle_answer(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
		manager_user_email: str | None,
		manager_user_hint: str | None,
		progress: asyncio.Queue[str] | None = None,
	) -> str:
		if not self.oracle_browser:
			raise OracleBrowserError("Oracle browser mode is disabled")
		text = latest_user_text(request)
		if not text:
			raise ValueError("A non-empty user message is required")
		if recipe_answer := await self._recipe_import_answer(
			text,
			user_id=user_id,
			conversation_id=conversation_id,
			manager_user_email=manager_user_email,
			manager_user_hint=manager_user_hint,
		):
			return recipe_answer
		proxy = self.app_server.dynamic_tool_proxy
		if not proxy:
			raise OracleBrowserError("Business tool proxy is unavailable")
		identity = tool_identity(
			email=manager_user_email,
			user_hint=manager_user_hint,
			mcp_url=self.settings.frappe_mcp_url,
			audience=self.settings.identity_audience,
			site_host=self.settings.frappe_site_host,
		)
		tool_specs = await proxy.specs()
		compact_tools = [
			{
				"name": spec.get("name"),
				"description": spec.get("description"),
				"parameters": spec.get("inputSchema") or {"type": "object"},
			}
			for spec in tool_specs
		]
		tools_json = json.dumps(compact_tools, ensure_ascii=False, separators=(",", ":"))
		if len(tools_json) > 19_000:
			tools_json = tools_json[:19_000]
		conversation_key = self._oracle_conversation_key(user_id, conversation_id)
		initial_prompt = f"""你是 child.myyr.top 的童健云业务智能助手，也是本轮任务的主推理模型。
只处理当前登录用户有权限访问的 child 站点业务，默认用简体中文。
你可以直接回答，也可以逐步调用下列受控业务工具。不要声称尚未验证的操作已经成功。
所有回复必须是一个 JSON 对象，不要使用 Markdown 代码围栏，也不要输出 JSON 之外的文字：
1. 最终回答：{{"action":"reply","content":"给用户的完整回答"}}
2. 调用工具：{{"action":"tool","tool":"工具名称","arguments":{{...}}}}
一次只调用一个工具。工具返回后再判断下一步；不要自行生成 actor_token，也不要询问或泄露凭据、内部地址和部署细节。
允许的工具：{tools_json}

用户请求：{text}"""
		lock_key = f"{user_id}\0{conversation_id}"
		async with self.locks[lock_key]:
			prompt = initial_prompt
			max_tool_rounds = getattr(self.settings, "oracle_browser_max_tool_rounds", 5)
			for round_index in range(max_tool_rounds + 1):
				if progress is not None:
					await progress.put("- 正在分析请求并规划下一步。\n")
				result = await self.oracle_browser.ask(
					prompt=prompt,
					conversation_key=conversation_key,
				)
				action = parse_oracle_action(result.reply)
				action_type = str(action.get("action") or "reply").strip().lower()
				if action_type != "tool":
					content = str(action.get("content") or result.reply).strip()
					if not content:
						raise OracleBrowserError("Oracle browser returned an empty final response")
					return content
				if round_index >= max_tool_rounds:
					raise OracleBrowserError("Oracle browser exceeded the business tool round limit")
				tool = str(action.get("tool") or "").strip()
				arguments = action.get("arguments") or {}
				if not tool or not isinstance(arguments, dict):
					raise OracleBrowserError("Oracle browser returned an invalid tool action")
				if identity is None:
					prompt = """当前请求缺少可验证的登录身份，因此不能调用站点业务工具。
请不要猜测或声称已经读取、创建或修改任何业务数据。只返回一个 JSON 对象：
{"action":"reply","content":"请重新进入 AI 员工后再执行需要访问站点数据的操作。普通问答仍可继续。"}"""
					continue
				if progress is not None:
					await progress.put(f"- 正在{_tool_label(tool)}。\n")
				tool_result = await proxy.call(tool, arguments, identity=identity)
				tool_payload = json.dumps(tool_result, ensure_ascii=False, separators=(",", ":"))
				if len(tool_payload) > 20_000:
					tool_payload = f"{tool_payload[:20_000]}…"
				prompt = f"""受控业务工具已经返回结果：
工具：{tool}
结果：{tool_payload}
请继续处理。仍然只返回一个 JSON 对象：需要下一工具时返回 action=tool；任务完成时返回 action=reply，并在最终回答中准确说明已完成和未完成的事项。"""
		raise OracleBrowserError("Oracle browser did not finish the request")

	async def _thread(self, user_id: str, conversation_id: str) -> str:
		thread_id = self.store.get(user_id, conversation_id)
		if thread_id:
			try:
				await self.app_server.resume(thread_id)
				return thread_id
			except AppServerError:
				self.store.delete(user_id, conversation_id)
		thread_id = await self.app_server.start_thread(str(self.workspace_for(user_id)))
		self.store.set(user_id, conversation_id, thread_id)
		return thread_id

	async def _events(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
		manager_user_email: str | None = None,
		manager_user_hint: str | None = None,
	) -> AsyncIterator[dict[str, Any] | None]:
		text = latest_user_text(request)
		if not text:
			raise ValueError("A non-empty user message is required")
		if recipe_answer := await self._recipe_import_answer(
			text,
			user_id=user_id,
			conversation_id=conversation_id,
			manager_user_email=manager_user_email,
			manager_user_hint=manager_user_hint,
		):
			yield {
				"method": "item/completed",
				"params": {"item": {"type": "agentMessage", "text": recipe_answer}},
			}
			yield {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
			return
		identity = tool_identity(
			email=manager_user_email,
			user_hint=manager_user_hint,
			mcp_url=self.settings.frappe_mcp_url,
			audience=self.settings.identity_audience,
			site_host=self.settings.frappe_site_host,
		)
		if not self.app_server.dynamic_tool_proxy:
			text = with_trusted_identity_context(
				text,
				email=manager_user_email,
				user_hint=manager_user_hint,
				mcp_url=self.settings.frappe_mcp_url,
				secret=self.settings.identity_shared_secret,
				audience=self.settings.identity_audience,
			)
		lock_key = f"{user_id}\0{conversation_id}"
		async with self.locks[lock_key]:
			thread_id = await self._thread(user_id, conversation_id)
			turn_id = ""
			self.app_server.bind_tool_identity(thread_id, identity)
			try:
				async with self.app_server.subscribe(thread_id) as queue:
					result = await self.app_server.request(
						"turn/start",
						{
							"threadId": thread_id,
							"input": [{"type": "text", "text": text}],
							"model": self.settings.model,
							"approvalPolicy": "never",
							"cwd": str(self.workspace_for(user_id)),
						},
					)
					turn_id = str((result or {}).get("turn", {}).get("id") or "")
					while True:
						try:
							event = await asyncio.wait_for(
								queue.get(), timeout=self.settings.keepalive_seconds
							)
						except TimeoutError:
							yield None
							continue
						params = event.get("params") or {}
						event_turn = str(params.get("turnId") or params.get("turn", {}).get("id") or "")
						if turn_id and event_turn and event_turn != turn_id:
							continue
						yield event
						if event.get("method") == "bridge/processExited":
							raise AppServerError(
								str(params.get("message") or "Codex App Server exited during the turn")
							)
						if event.get("method") == "turn/completed":
							return
			except asyncio.CancelledError:
				if turn_id:
					try:
						await self.app_server.request(
							"turn/interrupt", {"threadId": thread_id, "turnId": turn_id}
						)
					except Exception:
						pass
				raise
			finally:
				self.app_server.clear_tool_identity(thread_id, identity)

	async def complete(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
		manager_user_email: str | None = None,
		manager_user_hint: str | None = None,
	) -> dict[str, Any]:
		if getattr(self, "oracle_browser", None):
			try:
				answer = await self._oracle_answer(
					request,
					user_id=user_id,
					conversation_id=conversation_id,
					manager_user_email=manager_user_email,
					manager_user_hint=manager_user_hint,
				)
				return completion_response(request.model, sanitize_public_text(answer))
			except (OracleBrowserError, OSError, TimeoutError):
				logger.exception("Oracle browser primary inference failed; using configured fallback")
		messages = AgentMessageCollector()
		status = "completed"
		error = ""
		async for event in self._events(
			request,
			user_id=user_id,
			conversation_id=conversation_id,
			manager_user_email=manager_user_email,
			manager_user_hint=manager_user_hint,
		):
			if event is None:
				continue
			method = event.get("method")
			params = event.get("params") or {}
			messages.consume(event)
			if method == "error" and not params.get("willRetry", False):
				error = str((params.get("error") or {}).get("message") or "")
			elif method == "turn/completed":
				turn = params.get("turn") or {}
				status = str(turn.get("status") or status)
				error = error or str((turn.get("error") or {}).get("message") or "")
		answer = messages.answer()
		if status != "completed":
			raise AppServerError(error or f"Codex turn ended with status {status}")
		if not answer:
			raise AppServerError("Codex completed without an assistant response")
		return completion_response(request.model, sanitize_public_text(answer))

	async def stream(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
		manager_user_email: str | None = None,
		manager_user_hint: str | None = None,
	) -> AsyncIterator[str]:
		completion_id = f"chatcmpl-{uuid.uuid4().hex}"
		yield stream_chunk(completion_id, request.model, {"role": "assistant", "content": ""})
		process_display = ProcessDisplay()
		yield stream_chunk(
			completion_id,
			request.model,
			{"reasoning_content": process_display.initial()},
		)
		if getattr(self, "oracle_browser", None):
			progress: asyncio.Queue[str] = asyncio.Queue()
			oracle_task = asyncio.create_task(
				self._oracle_answer(
					request,
					user_id=user_id,
					conversation_id=conversation_id,
					manager_user_email=manager_user_email,
					manager_user_hint=manager_user_hint,
					progress=progress,
				)
			)
			try:
				while not oracle_task.done():
					try:
						message = await asyncio.wait_for(
							progress.get(), timeout=self.settings.keepalive_seconds
						)
					except TimeoutError:
						yield ": keepalive\n\n"
					else:
						yield stream_chunk(
							completion_id,
							request.model,
							{"reasoning_content": message},
						)
				answer = await oracle_task
				yield stream_chunk(
					completion_id,
					request.model,
					{"reasoning_content": "- 处理完成，正在整理结果。\n"},
				)
				yield stream_chunk(
					completion_id,
					request.model,
					{"content": sanitize_public_text(answer)},
				)
				yield stream_chunk(completion_id, request.model, {}, finish_reason="stop")
				yield "data: [DONE]\n\n"
				return
			except asyncio.CancelledError:
				oracle_task.cancel()
				await asyncio.gather(oracle_task, return_exceptions=True)
				raise
			except (OracleBrowserError, OSError, TimeoutError):
				logger.exception("Oracle browser primary inference failed; using configured fallback")
				yield stream_chunk(
					completion_id,
					request.model,
					{"reasoning_content": "- 主推理暂时不可用，正在切换备用推理。\n"},
				)
		messages = AgentMessageCollector()
		status = "completed"
		error = ""
		try:
			async for event in self._events(
				request,
				user_id=user_id,
				conversation_id=conversation_id,
				manager_user_email=manager_user_email,
				manager_user_hint=manager_user_hint,
			):
				if event is None:
					yield ": keepalive\n\n"
					continue
				method = event.get("method")
				params = event.get("params") or {}
				for process_delta in process_display.consume(event):
					yield stream_chunk(
						completion_id,
						request.model,
						{"reasoning_content": process_delta},
					)
				messages.consume(event)
				if method == "error" and not params.get("willRetry", False):
					error = str((params.get("error") or {}).get("message") or "")
				elif method == "turn/completed":
					turn = params.get("turn") or {}
					status = str(turn.get("status") or status)
					error = error or str((turn.get("error") or {}).get("message") or "")
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			status = "failed"
			error = str(exc)
		answer = messages.answer()
		if status != "completed":
			reference = uuid.uuid4().hex[:10].upper()
			logger.error(
				"I-ONE Agent turn failed reference=%s status=%s error=%s",
				reference,
				status,
				error,
			)
			answer = public_error_message(reference)
		elif not answer:
			reference = uuid.uuid4().hex[:10].upper()
			logger.error("I-ONE Agent returned no response reference=%s", reference)
			answer = public_error_message(reference)
		if answer:
			yield stream_chunk(
				completion_id,
				request.model,
				{"content": sanitize_public_text(answer)},
			)
		yield stream_chunk(completion_id, request.model, {}, finish_reason="stop")
		yield "data: [DONE]\n\n"
