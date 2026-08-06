from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.settings import Settings

TERMINAL_STATUSES = {"completed", "failed", "stopped", "cancelled"}


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


def run_answer(run: dict[str, Any]) -> str:
	status = str(run.get("status") or "").lower()
	if status == "completed":
		return str(run.get("response_text") or run.get("current_stage") or "任务已完成。")
	return str(run.get("error_message") or run.get("current_stage") or "任务未能完成。")


class LibreChatBridge:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings
		self.frappe = FrappeAgentClient(settings)
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
		message = next(
			(text for item in reversed(request.messages) if item.role == "user" and (text := message_text(item))),
			"",
		)
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
		run_id, _ = await self.start(
			request,
			user_id=user_id,
			conversation_id=conversation_id,
		)
		completion_id = f"chatcmpl-{uuid.uuid4().hex}"
		yield stream_chunk(completion_id, request.model, {"role": "assistant", "content": ""})
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
