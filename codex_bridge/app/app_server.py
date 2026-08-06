from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

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
		self.generation = 0

	@property
	def alive(self) -> bool:
		return self.process is not None and self.process.returncode is None

	async def start(self) -> None:
		if self.alive:
			return
		async with self.start_lock:
			if self.alive:
				return
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
					}
				},
			)
			await self.notify("initialized", {})

	async def stop(self) -> None:
		process = self.process
		self.process = None
		if process and process.returncode is None:
			process.terminate()
			try:
				await asyncio.wait_for(process.wait(), timeout=10)
			except TimeoutError:
				process.kill()
				await process.wait()
		for task in (self.reader_task, self.stderr_task):
			if task:
				task.cancel()
		await asyncio.gather(
			*(task for task in (self.reader_task, self.stderr_task) if task),
			return_exceptions=True,
		)
		self._fail_pending(AppServerError("Codex App Server stopped"))

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
		assert self.process and self.process.stdout
		try:
			while line := await self.process.stdout.readline():
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
		except Exception:
			logger.exception("Codex App Server stdout reader failed")
		finally:
			self.loaded_threads.clear()
			self._fail_pending(AppServerError("Codex App Server exited"))
			await self._publish({"method": "bridge/processExited", "params": {}})

	async def _read_stderr(self) -> None:
		assert self.process and self.process.stderr
		try:
			while line := await self.process.stderr.readline():
				logger.info("codex: %s", line.decode(errors="replace").rstrip())
		except asyncio.CancelledError:
			raise

	async def _handle_server_request(self, message: dict[str, Any]) -> None:
		method = str(message.get("method") or "")
		if method in {
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
		targets = list(self.subscribers.get(str(thread_id), ())) if thread_id else [
			queue for queues in self.subscribers.values() for queue in queues
		]
		for queue in targets:
			await queue.put(message)

	def _fail_pending(self, error: Exception) -> None:
		for future in list(self.pending.values()):
			if not future.done():
				future.set_exception(error)

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
			},
		)
		thread_id = str((result or {}).get("thread", {}).get("id") or "")
		if not thread_id:
			raise AppServerError("Codex App Server did not return a thread id")
		self.loaded_threads.add(thread_id)
		return thread_id

