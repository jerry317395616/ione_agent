from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.clients import DeepSeekClient, QwenClient
from app.settings import Settings

TERMINAL_STATUSES = {"completed", "failed", "stopped", "cancelled"}
SIMPLE_CHAT_PATTERN = re.compile(
	r"^(?:你|您)?好(?:呀|啊|哇)?[!！。.]?|^(?:hi|hello|hey)[!！。.]?$|^在吗[?？]?$",
	re.IGNORECASE,
)
LEAD_OBJECTS = ("线索", "招标", "投标", "采购公告", "商机", "获客")
LEAD_ACTIONS = ("找", "搜", "搜索", "收集", "整理", "发现", "监测", "抓取")
TASK_ACTIONS = ("创建", "新增", "修改", "删除", "填写", "写入", "导入", "发送", "执行", "运行")
TASK_OBJECTS = ("客户", "供应商", "员工", "线索", "商机", "记录", "单据", "附件", "CRM", "ERPNext", "Frappe")
INFORMATIONAL_PREFIXES = ("如何", "怎么", "怎样", "为什么", "什么是", "介绍", "说明", "请问如何", "请问怎么")
ROUTING_SYSTEM_PROMPT = """你是 I-ONE Agent 的请求路由器。判断用户最后一条消息应该进入哪条路径。
- chat：问候、知识问答、解释、建议、方案讨论、假设性问题，不需要真正调用外部工具或修改业务数据。
- task：要求联网搜索，或要求创建、更新、删除、导入、发送、运行、写入 Frappe/CRM 等真实操作。
只输出 JSON：{"route":"chat|task","confidence":0到1}。
例："你好"是 chat；"怎么利用 CRM 线索"是 chat；"帮我找医疗行业招标并写入 CRM"是 task。"""
GENERAL_CHAT_SYSTEM_PROMPT = """你是 I-ONE Agent，一名严谨、友好的企业智能助手。
优先使用中文，直接回答用户的问题，并结合对话上下文保持连续性。
当前路径只负责对话、解释和方案建议；不要声称已经搜索互联网、调用工具、创建记录或修改系统数据。
如果用户明确要求执行真实业务操作，简要说明需要进入任务执行流程。"""
CONVERSATION_UNAVAILABLE_MESSAGE = "AI 对话服务暂时不可用，请稍后重试。"


class ChatMessage(BaseModel):
	model_config = ConfigDict(extra="ignore")

	role: str
	content: Any = ""


class ChatCompletionRequest(BaseModel):
	model_config = ConfigDict(extra="ignore")

	model: str = "ione-agent"
	messages: list[ChatMessage] = Field(min_length=1)
	stream: bool = False


class ConversationStore:
	def __init__(self, path: Path) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(path, check_same_thread=False)
		self.lock = threading.RLock()
		with self.lock, self.connection:
			self.connection.execute("PRAGMA journal_mode=WAL")
			self.connection.execute("PRAGMA synchronous=FULL")
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS librechat_conversations (
				 librechat_user_id TEXT NOT NULL,
				 conversation_id TEXT NOT NULL,
				 frappe_session TEXT NOT NULL,
				 updated_at INTEGER NOT NULL,
				 PRIMARY KEY (librechat_user_id, conversation_id)
				)
				"""
			)

	def get(self, user_id: str, conversation_id: str) -> str | None:
		with self.lock:
			row = self.connection.execute(
				"""SELECT frappe_session FROM librechat_conversations
				WHERE librechat_user_id=? AND conversation_id=?""",
				(user_id, conversation_id),
			).fetchone()
		return str(row[0]) if row else None

	def set(self, user_id: str, conversation_id: str, frappe_session: str) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"""INSERT INTO librechat_conversations
				(librechat_user_id,conversation_id,frappe_session,updated_at)
				VALUES (?,?,?,?) ON CONFLICT(librechat_user_id,conversation_id)
				DO UPDATE SET frappe_session=excluded.frappe_session,updated_at=excluded.updated_at""",
				(user_id, conversation_id, frappe_session, int(time.time())),
			)


class FrappeAgentClient:
	def __init__(self, settings: Settings) -> None:
		headers = {
			"Authorization": f"token {settings.frappe_api_key}:{settings.frappe_api_secret}",
			"Accept": "application/json",
		}
		if settings.frappe_host_header:
			headers["Host"] = settings.frappe_host_header
		self.client = httpx.AsyncClient(
			base_url=settings.frappe_base_url,
			headers=headers,
			verify=settings.frappe_verify_tls,
			timeout=httpx.Timeout(30),
		)

	async def close(self) -> None:
		await self.client.aclose()

	async def call(self, method: str, **arguments: Any) -> Any:
		response = await self.client.post(
			f"/api/method/ione_agent.api.{method}",
			data={key: value for key, value in arguments.items() if value not in (None, "")},
		)
		try:
			payload = response.json()
		except ValueError as exc:
			raise RuntimeError(f"Frappe returned HTTP {response.status_code} without JSON") from exc
		if response.is_error or payload.get("exception"):
			message = payload.get("message") or payload.get("exception") or response.reason_phrase
			raise RuntimeError(f"Frappe request failed: {message}")
		return payload.get("message")

	async def send_message(self, message: str, session: str | None) -> dict[str, Any]:
		result = await self.call("send_message", message=message, session=session)
		if not isinstance(result, dict) or not result.get("run"):
			raise RuntimeError("Frappe did not return an agent run")
		return result

	async def get_run(self, run_id: str) -> dict[str, Any]:
		result = await self.call("get_run", run=run_id)
		if not isinstance(result, dict):
			raise RuntimeError("Frappe returned an invalid agent run")
		return result

	async def stop_run(self, run_id: str) -> None:
		try:
			await self.call("stop_run", run=run_id)
		except Exception:
			pass


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
	return next(
		(text for item in reversed(request.messages) if item.role == "user" and (text := message_text(item))),
		"",
	)


def conversation_history(
	request: ChatCompletionRequest,
	*,
	max_messages: int = 24,
	max_characters: int = 24000,
) -> list[dict[str, str]]:
	selected: list[dict[str, str]] = []
	remaining = max_characters
	for item in reversed(request.messages):
		if item.role not in {"user", "assistant"}:
			continue
		content = message_text(item)
		if not content:
			continue
		content = content[:remaining]
		if not content:
			break
		selected.append({"role": item.role, "content": content})
		remaining -= len(content)
		if len(selected) >= max_messages or remaining <= 0:
			break
	selected.reverse()
	return selected


def fallback_route(message: str) -> str:
	normalized = message.strip()
	if SIMPLE_CHAT_PATTERN.fullmatch(normalized):
		return "chat"
	if normalized.startswith(INFORMATIONAL_PREFIXES):
		return "chat"
	if any(word in normalized for word in LEAD_OBJECTS) and any(
		word in normalized for word in LEAD_ACTIONS
	):
		return "task"
	if any(word in normalized for word in TASK_ACTIONS) and any(
		word.lower() in normalized.lower() for word in TASK_OBJECTS
	):
		return "task"
	return "chat"


class ConversationModel:
	def __init__(
		self,
		settings: Settings,
		*,
		deepseek: DeepSeekClient | None = None,
		qwen: QwenClient | None = None,
	) -> None:
		self.settings = settings
		self.deepseek = deepseek or DeepSeekClient(settings)
		self.qwen = qwen or QwenClient(settings)

	def route(self, message: str) -> str:
		local_route = fallback_route(message)
		if SIMPLE_CHAT_PATTERN.fullmatch(message.strip()):
			return "chat"
		if local_route == "task":
			return "task"
		try:
			result = self.deepseek.json(
				ROUTING_SYSTEM_PROMPT,
				json.dumps({"message": message}, ensure_ascii=False),
				{"route": local_route, "confidence": 0},
				model=self.settings.deepseek_fast_model,
				timeout=20,
				max_attempts=1,
				max_tokens=300,
				thinking=False,
				purpose="librechat_route",
			)
		except Exception:
			return local_route
		route = result.get("route") if isinstance(result, dict) else local_route
		return route if route in {"chat", "task"} else local_route

	def answer(self, request: ChatCompletionRequest) -> str:
		history = conversation_history(request)
		if not history or history[-1]["role"] != "user":
			raise ValueError("A non-empty user message is required")
		try:
			return self.deepseek.chat_messages(
				GENERAL_CHAT_SYSTEM_PROMPT,
				history,
				model=self.settings.deepseek_fast_model,
				timeout=90,
				max_attempts=2,
				max_tokens=4000,
				thinking=False,
				purpose="librechat_conversation",
			)
		except Exception:
			transcript = "\n\n".join(
				f"{item['role']}: {item['content']}" for item in history
			)
			try:
				return self.qwen.chat(
					GENERAL_CHAT_SYSTEM_PROMPT,
					transcript,
					timeout=90,
					max_attempts=1,
					purpose="librechat_conversation_fallback",
				)
			except Exception:
				return CONVERSATION_UNAVAILABLE_MESSAGE


def run_answer(run: dict[str, Any]) -> str:
	status = str(run.get("status") or "").lower()
	if status == "completed":
		return str(run.get("response_text") or run.get("current_stage") or "任务已完成。")
	return str(run.get("error_message") or run.get("current_stage") or "任务未能完成。")


class LibreChatBridge:
	def __init__(
		self,
		settings: Settings,
		*,
		conversation_model: ConversationModel | None = None,
	) -> None:
		self.settings = settings
		self.frappe = FrappeAgentClient(settings)
		self.conversation_model = conversation_model or ConversationModel(settings)
		self.conversations = ConversationStore(settings.data_dir / "librechat.sqlite3")

	async def close(self) -> None:
		await self.frappe.close()

	async def start(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
	) -> tuple[str, str]:
		message = latest_user_text(request)
		if not message:
			raise ValueError("A non-empty user message is required")
		session = self.conversations.get(user_id, conversation_id)
		result = await self.frappe.send_message(message, session)
		session = str(result["session"])
		self.conversations.set(user_id, conversation_id, session)
		run = result["run"]
		return str(run["name"]), session

	async def wait(self, run_id: str) -> dict[str, Any]:
		deadline = time.monotonic() + self.settings.librechat_run_timeout_seconds
		while True:
			run = await self.frappe.get_run(run_id)
			if str(run.get("status") or "").lower() in TERMINAL_STATUSES:
				return run
			if time.monotonic() >= deadline:
				await self.frappe.stop_run(run_id)
				raise TimeoutError("I-ONE Agent run exceeded its execution budget")
			await asyncio.sleep(2)

	async def complete(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
	) -> dict[str, Any]:
		message = latest_user_text(request)
		if not message:
			raise ValueError("A non-empty user message is required")
		route = await asyncio.to_thread(self.conversation_model.route, message)
		if route == "chat":
			answer = await asyncio.to_thread(self.conversation_model.answer, request)
			return completion_response(request.model, answer)
		run_id, _ = await self.start(
			request,
			user_id=user_id,
			conversation_id=conversation_id,
		)
		run = await self.wait(run_id)
		answer = run_answer(run)
		return completion_response(request.model, answer)

	async def stream(
		self,
		request: ChatCompletionRequest,
		*,
		user_id: str,
		conversation_id: str,
	) -> AsyncIterator[str]:
		completion_id = f"chatcmpl-{uuid.uuid4().hex}"
		yield stream_chunk(completion_id, request.model, {"role": "assistant", "content": ""})
		message = latest_user_text(request)
		if not message:
			raise ValueError("A non-empty user message is required")
		route_task = asyncio.create_task(
			asyncio.to_thread(self.conversation_model.route, message)
		)
		while not route_task.done():
			await asyncio.wait({route_task}, timeout=5)
			if not route_task.done():
				yield ": I-ONE Agent is routing\n\n"
		route = await route_task
		if route == "chat":
			answer_task = asyncio.create_task(
				asyncio.to_thread(self.conversation_model.answer, request)
			)
			while not answer_task.done():
				await asyncio.wait({answer_task}, timeout=5)
				if not answer_task.done():
					yield ": I-ONE Agent is thinking\n\n"
			answer = await answer_task
			yield stream_chunk(completion_id, request.model, {"content": answer})
			yield stream_chunk(completion_id, request.model, {}, finish_reason="stop")
			yield "data: [DONE]\n\n"
			return
		run_id, _ = await self.start(
			request,
			user_id=user_id,
			conversation_id=conversation_id,
		)
		deadline = time.monotonic() + self.settings.librechat_run_timeout_seconds
		try:
			while True:
				run = await self.frappe.get_run(run_id)
				if str(run.get("status") or "").lower() in TERMINAL_STATUSES:
					break
				if time.monotonic() >= deadline:
					await self.frappe.stop_run(run_id)
					raise TimeoutError("I-ONE Agent run exceeded its execution budget")
				yield ": I-ONE Agent is working\n\n"
				await asyncio.sleep(5)
		except asyncio.CancelledError:
			await self.frappe.stop_run(run_id)
			raise
		yield stream_chunk(completion_id, request.model, {"content": run_answer(run)})
		yield stream_chunk(completion_id, request.model, {}, finish_reason="stop")
		yield "data: [DONE]\n\n"


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
