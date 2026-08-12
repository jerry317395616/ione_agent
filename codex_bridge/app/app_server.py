from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.dynamic_tools import DynamicToolProxy
from app.identity import ToolIdentity
from app.settings import Settings

logger = logging.getLogger(__name__)


class AppServerError(RuntimeError):
	def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
		super().__init__(message)
		self.code = code
		self.data = data


class CodexAppServer:
	def __init__(self, settings: Settings) -> None:
		self.settings = settings
		self.process: asyncio.subprocess.Process | None = None
		self.reader_task: asyncio.Task | None = None
		self.stderr_task: asyncio.Task | None = None
		self.start_lock = asyncio.Lock()
		self.write_lock = asyncio.Lock()
		self.next_id = 1
		self.pending: dict[int, asyncio.Future] = {}
		self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
		self.loaded_threads: set[str] = set()
		self.active_tool_identities: dict[str, ToolIdentity] = {}
		self.generation = 0
		self.dynamic_tool_proxy = (
			DynamicToolProxy(settings)
			if getattr(settings, "mcp_enabled", False) and getattr(settings, "frappe_dynamic_tools", False)
			else None
		)

	@property
	def alive(self) -> bool:
		return (
			self.process is not None
			and self.process.returncode is None
			and self.reader_task is not None
			and not self.reader_task.done()
		)

	async def start(self) -> None:
		if self.alive:
			return
		async with self.start_lock:
			if self.alive:
				return
			if self.process is not None:
				await self._terminate_process(self.process)
				self.process = None
			self.settings.prepare()
			self.process = await asyncio.create_subprocess_exec(
				str(self.settings.codex_bin),
				"app-server",
				"--listen",
				"stdio://",
				stdin=asyncio.subprocess.PIPE,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
				cwd=self.settings.workspace_root,
				env=self.settings.process_environment(),
				limit=self.settings.app_server_message_limit_bytes,
			)
			self.generation += 1
			self.loaded_threads.clear()
			self.reader_task = asyncio.create_task(self._read_stdout(), name="codex-app-server-stdout")
			self.stderr_task = asyncio.create_task(self._read_stderr(), name="codex-app-server-stderr")
			await self._request_without_start(
				"initialize",
				{
					"clientInfo": {
						"name": "ione_agent",
						"title": "I-ONE Agent",
						"version": "1.0.0",
					},
					"capabilities": {"experimentalApi": True} if self.dynamic_tool_proxy else {},
				},
			)
			await self.notify("initialized", {})

	async def stop(self) -> None:
		process = self.process
		self.process = None
		reader_task = self.reader_task
		stderr_task = self.stderr_task
		self.reader_task = None
		self.stderr_task = None
		self.active_tool_identities.clear()
		for task in (reader_task, stderr_task):
			if task and task is not asyncio.current_task():
				task.cancel()
		if process:
			await self._terminate_process(process)
		await asyncio.gather(
			*(task for task in (reader_task, stderr_task) if task and task is not asyncio.current_task()),
			return_exceptions=True,
		)
		self._fail_pending(AppServerError("Codex App Server stopped"))

	async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
		if process.returncode is not None:
			return
		process.terminate()
		try:
			await asyncio.wait_for(process.wait(), timeout=10)
		except TimeoutError:
			process.kill()
			await process.wait()

	async def request(self, method: str, params: dict[str, Any]) -> Any:
		await self.start()
		return await self._request_without_start(method, params)

	async def _request_without_start(self, method: str, params: dict[str, Any]) -> Any:
		request_id = self.next_id
		self.next_id += 1
		future = asyncio.get_running_loop().create_future()
		self.pending[request_id] = future
		try:
			await self._write({"method": method, "id": request_id, "params": params})
			return await asyncio.wait_for(future, timeout=self.settings.request_timeout_seconds)
		finally:
			self.pending.pop(request_id, None)

	async def notify(self, method: str, params: dict[str, Any]) -> None:
		await self._write({"method": method, "params": params})

	async def _write(self, payload: dict[str, Any]) -> None:
		process = self.process
		if not process or process.returncode is not None or not process.stdin:
			raise AppServerError("Codex App Server is not running")
		data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
		async with self.write_lock:
			process.stdin.write(data)
			await process.stdin.drain()

	async def _read_stdout(self) -> None:
		process = self.process
		assert process and process.stdout
		failure = AppServerError("Codex App Server exited")
		try:
			while line := await process.stdout.readline():
				try:
					message = json.loads(line)
				except json.JSONDecodeError:
					logger.warning("Ignoring non-JSON app-server output")
					continue
				if "id" in message and "method" not in message:
					future = self.pending.get(message["id"])
					if future and not future.done():
						if "error" in message:
							error = message["error"] or {}
							future.set_exception(
								AppServerError(
									str(error.get("message") or "Codex App Server request failed"),
									code=error.get("code"),
									data=error.get("data"),
								)
							)
						else:
							future.set_result(message.get("result"))
					continue
				if "id" in message and "method" in message:
					await self._handle_server_request(message)
					continue
				await self._publish(message)
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			logger.exception("Codex App Server stdout reader failed")
			failure = AppServerError(f"Codex App Server output reader failed: {exc}")
		finally:
			owned_process = self.process is process
			stderr_task = self.stderr_task
			if owned_process:
				self.process = None
				self.reader_task = None
				self.stderr_task = None
			self.loaded_threads.clear()
			self._fail_pending(failure)
			await self._publish({"method": "bridge/processExited", "params": {"message": str(failure)}})
			if owned_process:
				if stderr_task and stderr_task is not asyncio.current_task():
					stderr_task.cancel()
				await self._terminate_process(process)

	async def _read_stderr(self) -> None:
		assert self.process and self.process.stderr
		try:
			while line := await self.process.stderr.readline():
				logger.info("codex: %s", line.decode(errors="replace").rstrip())
		except asyncio.CancelledError:
			raise

	async def _handle_server_request(self, message: dict[str, Any]) -> None:
		method = str(message.get("method") or "")
		if method == "item/tool/call" and self.dynamic_tool_proxy:
			params = message.get("params") or {}
			tool = str(params.get("tool") or "")
			arguments = params.get("arguments") or {}
			started_at = time.monotonic()
			public_params = {
				"threadId": params.get("threadId"),
				"turnId": params.get("turnId"),
				"callId": params.get("callId") or params.get("itemId") or str(message["id"]),
				"tool": tool,
			}
			await self._publish({"method": "bridge/dynamicTool/started", "params": public_params})
			if not isinstance(arguments, dict):
				result = DynamicToolProxy._failure("业务工具参数格式无效。")
			else:
				identity = self.active_tool_identities.get(str(params.get("threadId") or ""))
				result = await self.dynamic_tool_proxy.call(
					tool,
					arguments,
					identity=identity,
				)
			await self._publish(
				{
					"method": "bridge/dynamicTool/completed",
					"params": {
						**public_params,
						"success": result.get("success") is True,
						"durationMs": round((time.monotonic() - started_at) * 1000),
					},
				}
			)
		elif method in {
			"item/commandExecution/requestApproval",
			"item/fileChange/requestApproval",
			"execCommandApproval",
			"applyPatchApproval",
		}:
			result: dict[str, Any] = {"decision": "decline"}
		elif method == "permissions/requestApproval":
			result = {
				"permissions": {"fileSystem": None, "network": {"enabled": False}},
				"scope": "turn",
				"strictAutoReview": False,
			}
		else:
			await self._write(
				{
					"id": message["id"],
					"error": {"code": -32000, "message": "Interactive client request is disabled"},
				}
			)
			return
		await self._write({"id": message["id"], "result": result})

	async def _publish(self, message: dict[str, Any]) -> None:
		params = message.get("params") or {}
		thread_id = params.get("threadId")
		targets = (
			list(self.subscribers.get(str(thread_id), ()))
			if thread_id
			else [queue for queues in self.subscribers.values() for queue in queues]
		)
		for queue in targets:
			await queue.put(message)

	def _fail_pending(self, error: Exception) -> None:
		for future in list(self.pending.values()):
			if not future.done():
				future.set_exception(error)

	def bind_tool_identity(self, thread_id: str, identity: ToolIdentity | None) -> None:
		"""Bind an identity to one active thread without persisting credentials."""
		if identity is None:
			self.active_tool_identities.pop(thread_id, None)
		else:
			self.active_tool_identities[thread_id] = identity

	def clear_tool_identity(self, thread_id: str, identity: ToolIdentity | None) -> None:
		"""Clear only the binding owned by the completing turn."""
		if self.active_tool_identities.get(thread_id) is identity:
			self.active_tool_identities.pop(thread_id, None)

	@asynccontextmanager
	async def subscribe(self, thread_id: str) -> AsyncIterator[asyncio.Queue]:
		queue: asyncio.Queue = asyncio.Queue()
		self.subscribers[thread_id].add(queue)
		try:
			yield queue
		finally:
			self.subscribers[thread_id].discard(queue)
			if not self.subscribers[thread_id]:
				self.subscribers.pop(thread_id, None)

	async def resume(self, thread_id: str) -> None:
		await self.start()
		if thread_id in self.loaded_threads:
			return
		await self.request("thread/resume", {"threadId": thread_id})
		self.loaded_threads.add(thread_id)

	async def start_thread(self, workspace: str) -> str:
		dynamic_tools = await self.dynamic_tool_proxy.specs() if self.dynamic_tool_proxy else None
		result = await self.request(
			"thread/start",
			{
				"model": self.settings.model,
				"modelProvider": self.settings.model_provider,
				"cwd": workspace,
				"approvalPolicy": "never",
				"sandbox": self.settings.sandbox,
				"developerInstructions": self.settings.developer_instructions,
				"ephemeral": False,
				"dynamicTools": dynamic_tools,
			},
		)
		thread_id = str((result or {}).get("thread", {}).get("id") or "")
		if not thread_id:
			raise AppServerError("Codex App Server did not return a thread id")
		self.loaded_threads.add(thread_id)
		return thread_id
