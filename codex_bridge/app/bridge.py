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
from app.identity import with_trusted_identity_context
from app.public_output import public_error_message, sanitize_public_text
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

	reasoning_buffers: defaultdict[str, list[str]] = field(
		default_factory=lambda: defaultdict(list)
	)
	seen_summaries: set[str] = field(default_factory=set)

	@staticmethod
	def initial() -> str:
		return "### 处理过程\n\n- 已收到请求，正在分析。\n"

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
				return [f"- 正在{label}。\n"]
			status = "完成" if params.get("success", True) else "未成功"
			return [f"- {label}{status}{_duration_text(params.get('durationMs'))}。\n"]

		if method not in {"item/started", "item/completed", "turn/completed"}:
			return []
		if method == "turn/completed":
			turn = params.get("turn") or {}
			if str(turn.get("status") or "completed") == "completed":
				return ["- 处理完成，正在整理结果。\n"]
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
			return [f"- 正在{label}。\n"]
		status = str(item.get("status") or "completed")
		result = "完成" if status == "completed" else "未成功"
		return [f"- {label}{result}{_duration_text(item.get('durationMs'))}。\n"]


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
				try:
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

	async def complete(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
		manager_user_email: str | None = None,
		manager_user_hint: str | None = None,
	) -> dict[str, Any]:
		parts: list[str] = []
		completed_messages: list[str] = []
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
			if method == "item/agentMessage/delta":
				parts.append(str(params.get("delta") or ""))
			elif method == "item/completed":
				item = params.get("item") or {}
				if item.get("type") == "agentMessage" and item.get("text"):
					completed_messages.append(str(item["text"]))
			elif method == "error" and not params.get("willRetry", False):
				error = str((params.get("error") or {}).get("message") or "")
			elif method == "turn/completed":
				turn = params.get("turn") or {}
				status = str(turn.get("status") or status)
				error = error or str((turn.get("error") or {}).get("message") or "")
		answer = "".join(parts).strip() or (completed_messages[-1].strip() if completed_messages else "")
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
		parts: list[str] = []
		completed_messages: list[str] = []
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
				if method == "item/agentMessage/delta" and (
					delta := str(params.get("delta") or "")
				):
					parts.append(delta)
				elif method == "item/completed":
					item = params.get("item") or {}
					if item.get("type") == "agentMessage" and item.get("text"):
						completed_messages.append(str(item["text"]))
				elif method == "error" and not params.get("willRetry", False):
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
		answer = "".join(parts).strip() or (completed_messages[-1].strip() if completed_messages else "")
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
