from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.app_server import AppServerError, CodexAppServer
from app.public_output import public_error_message, sanitize_public_text
from app.settings import Settings
from app.store import ConversationStore


logger = logging.getLogger(__name__)


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
	) -> AsyncIterator[dict[str, Any] | None]:
		text = latest_user_text(request)
		if not text:
			raise ValueError("A non-empty user message is required")
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
	) -> dict[str, Any]:
		parts: list[str] = []
		completed_messages: list[str] = []
		status = "completed"
		error = ""
		async for event in self._events(
			request, user_id=user_id, conversation_id=conversation_id
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
	) -> AsyncIterator[str]:
		completion_id = f"chatcmpl-{uuid.uuid4().hex}"
		yield stream_chunk(completion_id, request.model, {"role": "assistant", "content": ""})
		parts: list[str] = []
		completed_messages: list[str] = []
		status = "completed"
		error = ""
		try:
			async for event in self._events(
				request, user_id=user_id, conversation_id=conversation_id
			):
				if event is None:
					yield ": keepalive\n\n"
					continue
				method = event.get("method")
				params = event.get("params") or {}
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
