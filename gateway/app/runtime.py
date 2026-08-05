from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from app.settings import Settings
from app.store import RunStore, utc_now

WINDOWS_EXECUTION_GUIDANCE = (
	"Windows execution rule: run_shell only launches an allow-listed application executable. "
	"Never use run_shell for shell scripts, redirection, cmd.exe, or PowerShell. "
	"Open the target application directly (for example excel.exe, winword.exe, or notepad.exe), "
	"then complete the task through UI automation. Include this rule in every Windows task description and tips. "
	"The final user-facing result must be concise Simplified Chinese. Never return raw JSON, internal task "
	"objects, constellation data, chain-of-thought, or debug output."
)

FAILED_RESULT_STATES = {"fail", "failed", "error", "cancelled", "canceled"}
DEVICE_KEEPALIVE_INTERVAL_SECONDS = 20


def device_heartbeat_message() -> str:
	return json.dumps({"type": "heartbeat", "status": "ok"}, separators=(",", ":"))


def heartbeat_client_type(client_id: str) -> str:
	return "constellation" if "@" in client_id else "device"


def patch_constellation_heartbeat() -> None:
	"""Keep UFO main's Galaxy heartbeat bound to its registered role.

	UFO main currently registers Galaxy connections as ``constellation`` but
	uses a heartbeat whose model default is ``device``. The hardened UFO server
	rejects that mismatch. This process-local compatibility patch affects Galaxy
	only; the official UFO source and separate device server remain unchanged.
	"""
	from aip.messages import ClientMessage, ClientMessageType, ClientType, TaskStatus
	from aip.protocol.heartbeat import HeartbeatProtocol

	if getattr(HeartbeatProtocol.send_heartbeat, "_ione_constellation_compatible", False):
		return

	async def send_heartbeat(self, client_id: str, metadata: dict[str, Any] | None = None) -> None:
		client_type = ClientType(heartbeat_client_type(client_id))
		message = ClientMessage(
			type=ClientMessageType.HEARTBEAT,
			client_id=client_id,
			client_type=client_type,
			status=TaskStatus.OK,
			timestamp=utc_now(),
			metadata=metadata,
		)
		await self.send_message(message)

	send_heartbeat._ione_constellation_compatible = True
	HeartbeatProtocol.send_heartbeat = send_heartbeat


def probe_websocket_server(host: str, port: int, path: str, timeout: float = 5) -> bool:
	key = base64.b64encode(os.urandom(16)).decode("ascii")
	request = (
		f"GET {path} HTTP/1.1\r\n"
		f"Host: {host}:{port}\r\n"
		"Upgrade: websocket\r\n"
		"Connection: Upgrade\r\n"
		f"Sec-WebSocket-Key: {key}\r\n"
		"Sec-WebSocket-Version: 13\r\n\r\n"
	).encode("ascii")
	try:
		with socket.create_connection((host, port), timeout=timeout) as connection:
			connection.settimeout(timeout)
			connection.sendall(request)
			response = connection.recv(4096)
	except OSError:
		return False
	return response.startswith(b"HTTP/1.1 101")


def git_commit(repo: Path) -> str:
	try:
		return subprocess.check_output(
			["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=5
		).strip()
	except (OSError, subprocess.SubprocessError):
		return "unknown"


def configure_ufo(settings: Settings, devices: list[dict[str, Any]] | None = None) -> None:
	system_path = settings.ufo_root / "config" / "ufo" / "system.yaml"
	system = yaml.safe_load(system_path.read_text(encoding="utf-8")) or {}
	system["TOP_P"] = 0.8
	system["MAX_STEP"] = settings.max_step
	system["MAX_RETRY"] = 3
	system["TIMEOUT"] = 120
	system["REQUEST_TIMEOUT"] = 120
	system_path.write_text(yaml.safe_dump(system, allow_unicode=True, sort_keys=False), encoding="utf-8")

	config_dir = settings.ufo_root / "config" / "galaxy"
	config_dir.mkdir(parents=True, exist_ok=True)
	agent_config = {
		"CONSTELLATION_AGENT": {
			"REASONING_MODEL": False,
			"API_TYPE": "openai",
			"API_BASE": settings.openai_base_url,
			"API_KEY": settings.qwen_api_key,
			"API_MODEL": settings.qwen_model,
			"CONSTELLATION_CREATION_PROMPT": "galaxy/prompts/constellation/share/constellation_creation.yaml",
			"CONSTELLATION_EDITING_PROMPT": "galaxy/prompts/constellation/share/constellation_editing.yaml",
			"CONSTELLATION_CREATION_EXAMPLE_PROMPT": "galaxy/prompts/constellation/examples/constellation_creation_example.yaml",
			"CONSTELLATION_EDITING_EXAMPLE_PROMPT": "galaxy/prompts/constellation/examples/constellation_editing_example.yaml",
		}
	}
	(config_dir / "agent.yaml").write_text(
		yaml.safe_dump(agent_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
	)
	ufo_agents = {
		"HOST_AGENT": {
			"VISUAL_MODE": False,
			"REASONING_MODEL": False,
			"API_TYPE": "openai",
			"API_BASE": settings.openai_base_url,
			"API_KEY": settings.qwen_api_key,
			"API_MODEL": settings.qwen_model,
			"PROMPT": "ufo/prompts/share/base/host_agent.yaml",
			"EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/host_agent_example.yaml",
		},
		"APP_AGENT": {
			"VISUAL_MODE": False,
			"REASONING_MODEL": False,
			"API_TYPE": "openai",
			"API_BASE": settings.openai_base_url,
			"API_KEY": settings.qwen_api_key,
			"API_MODEL": settings.qwen_model,
			"PROMPT": "ufo/prompts/share/base/app_agent.yaml",
			"EXAMPLE_PROMPT": "ufo/prompts/examples/{mode}/app_agent_example.yaml",
			"EXAMPLE_PROMPT_AS": "ufo/prompts/examples/{mode}/app_agent_example_as.yaml",
		},
	}
	(settings.ufo_root / "config" / "ufo" / "agents.yaml").write_text(
		yaml.safe_dump(ufo_agents, allow_unicode=True, sort_keys=False), encoding="utf-8"
	)
	device_config = {"devices": devices if devices is not None else settings.devices.get("devices", [])}
	(config_dir / "devices.yaml").write_text(
		yaml.safe_dump(device_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
	)
	constellation_path = config_dir / "constellation.yaml"
	constellation = yaml.safe_load(constellation_path.read_text(encoding="utf-8")) or {}
	constellation["MAX_STEP"] = settings.max_step
	constellation["TOP_P"] = 0.8
	constellation["DEVICE_INFO"] = "config/galaxy/devices.yaml"
	constellation_path.write_text(
		yaml.safe_dump(constellation, allow_unicode=True, sort_keys=False), encoding="utf-8"
	)


def _strings(value: Any, *, path: str = ""):
	if isinstance(value, str) and value.strip():
		yield path, value.strip()
	elif isinstance(value, dict):
		for key, item in value.items():
			yield from _strings(item, path=f"{path}.{key}" if path else str(key))
	elif isinstance(value, (list, tuple)):
		for index, item in enumerate(value):
			yield from _strings(item, path=f"{path}[{index}]")


def _session_results(result: dict[str, Any]) -> dict[str, Any]:
	value = result.get("session_results")
	return value if isinstance(value, dict) else {}


def result_failed(result: dict[str, Any]) -> bool:
	"""Honor Galaxy's nested outcome instead of its transport-level status."""
	session_results = _session_results(result)
	stats = session_results.get("final_constellation_stats")
	constellation = result.get("constellation")
	states = [result.get("status"), session_results.get("status")]
	if isinstance(stats, dict):
		states.append(stats.get("state"))
	if isinstance(constellation, dict):
		states.append(constellation.get("state"))
	return any(str(value or "").strip().lower() in FAILED_RESULT_STATES for value in states)


def _constellation_summary(value: Any) -> str:
	if not isinstance(value, dict):
		return ""
	tasks = value.get("tasks")
	if not isinstance(tasks, dict) or not tasks:
		return ""
	statuses = [
		str(task.get("status") or "").strip().lower()
		for task in tasks.values()
		if isinstance(task, dict)
	]
	failed = sum(status in FAILED_RESULT_STATES for status in statuses)
	completed = sum(status in {"complete", "completed", "success", "succeeded"} for status in statuses)
	if failed:
		return f"任务执行失败：共执行 {len(statuses)} 个步骤，其中 {failed} 个失败。"
	if completed:
		return f"任务已完成：共执行 {len(statuses)} 个步骤，其中 {completed} 个完成。"
	return ""


def _clean_result_text(value: Any) -> str:
	if not isinstance(value, str):
		return ""
	text = value.strip()
	if not text:
		return ""
	if text.startswith(("{", "[")):
		try:
			return _constellation_summary(json.loads(text))
		except (TypeError, ValueError):
			return ""
	return text[:4000]


def _final_result_text(result: dict[str, Any]) -> str:
	final_results = _session_results(result).get("final_results")
	if not isinstance(final_results, list):
		return ""
	for item in reversed(final_results):
		if isinstance(item, dict):
			for key in ("final_answer", "answer", "summary", "result", "response", "message", "output"):
				if text := _clean_result_text(item.get(key)):
					return text
		elif text := _clean_result_text(item):
			return text
	return ""


def extract_answer(result: dict[str, Any], events: list[dict[str, Any]]) -> str:
	if text := _final_result_text(result):
		return text
	preferred = ("final_answer", "answer", "response", "output", "result", "summary", "message")
	candidates: list[tuple[int, int, str]] = []
	for path, value in _strings(result.get("session_results")):
		if not (value := _clean_result_text(value)):
			continue
		score = next((100 - index for index, key in enumerate(preferred) if key in path.lower()), 0)
		candidates.append((score, len(value), value))
	for event in events:
		for path, value in _strings(event.get("output_data") or event.get("result") or event.get("data")):
			if not (value := _clean_result_text(value)):
				continue
			score = 80 if event.get("event_type", "").startswith("agent") else 10
			if any(key in path.lower() for key in preferred):
				score += 20
			candidates.append((score, len(value), value))
	if not candidates:
		return ""
	return max(candidates, key=lambda item: (item[0], item[1]))[2]


def failure_message(result: dict[str, Any]) -> str:
	session_results = _session_results(result)
	stats = session_results.get("final_constellation_stats")
	counts = stats.get("task_status_counts", {}) if isinstance(stats, dict) else {}
	failed = int(counts.get("failed") or 0) if isinstance(counts, dict) else 0
	total = int(stats.get("total_tasks") or 0) if isinstance(stats, dict) else 0
	if failed:
		prefix = f"任务执行失败：共尝试 {total or failed} 个步骤，{failed} 个失败。"
	else:
		prefix = "任务执行失败。"
	return (
		f"{prefix} 设备未能完成操作。请确认目标应用已安装、电脑桌面已解锁且设备保持在线，然后重试。"
	)


def build_prompt(request: str, history: list[dict[str, str]]) -> str:
	if not history:
		return f"{WINDOWS_EXECUTION_GUIDANCE}\n\n{request}"
	lines = ["以下是同一对话中的最近上下文，仅用于理解当前任务："]
	lines.insert(0, "")
	lines.insert(0, WINDOWS_EXECUTION_GUIDANCE)
	for item in history[-12:]:
		role = "用户" if item.get("role") == "user" else "智能体"
		lines.append(f"{role}：{item.get('content', '')}")
	lines.extend(["", "当前用户任务：", request])
	return "\n".join(lines)


class StoreObserver:
	def __init__(self, store: RunStore) -> None:
		self.store = store
		self.run_id: str | None = None
		self.count = 0
		self.serializer = None

	async def on_event(self, event) -> None:
		if not self.run_id:
			return
		if self.serializer is None:
			from galaxy.webui.websocket_observer import EventSerializer

			self.serializer = EventSerializer()
		serialized = self.serializer.serialize_event(event)
		self.store.append_event(self.run_id, serialized)
		self.count += 1
		stage = serialized.get("data", {}).get("message") or serialized.get("event_type", "UFO3 执行中")
		progress = min(92, 8 + self.count * 3)
		self.store.update(self.run_id, current_stage=str(stage)[:240], progress=progress)


class UFORuntime:
	def __init__(self, settings: Settings, store: RunStore, device_store=None) -> None:
		self.settings = settings
		self.store = store
		self.device_store = device_store
		self.commit = git_commit(settings.ufo_root)
		self.queue: asyncio.Queue[str] = asyncio.Queue()
		self.client = None
		self.current_run_id: str | None = None
		self.worker_task: asyncio.Task | None = None
		self.device_server_watchdog_thread: threading.Thread | None = None
		self.device_server_watchdog_stop = threading.Event()
		self.device_server_watchdog_checks = 0
		self.device_server_process: asyncio.subprocess.Process | None = None
		self.device_server_lock = asyncio.Lock()
		self.devices_dirty = False
		self.observer_subscribed = False
		self.observer = StoreObserver(store)

	async def start(self) -> None:
		configure_ufo(self.settings, self._device_config())
		os.chdir(self.settings.ufo_root)
		if str(self.settings.ufo_root) not in sys.path:
			sys.path.insert(0, str(self.settings.ufo_root))
		await self._start_device_server()
		self.device_server_watchdog_thread = threading.Thread(
			target=self._watch_device_server,
			name="ione-ufo-device-server-watchdog",
			daemon=True,
		)
		self.device_server_watchdog_thread.start()
		for run_id in self.store.recoverable():
			await self.queue.put(run_id)
		self.worker_task = asyncio.create_task(self._worker(), name="ione-ufo-runner")

	async def close(self) -> None:
		if self.worker_task:
			self.worker_task.cancel()
			try:
				await self.worker_task
			except asyncio.CancelledError:
				pass
		self.device_server_watchdog_stop.set()
		if self.device_server_watchdog_thread:
			self.device_server_watchdog_thread.join(timeout=2)
		if self.client:
			await self.client.shutdown(force=True)
		if self.device_server_process and self.device_server_process.returncode is None:
			self.device_server_process.terminate()
			try:
				await asyncio.wait_for(self.device_server_process.wait(), timeout=8)
			except TimeoutError:
				self.device_server_process.kill()

	def _device_config(self) -> list[dict[str, Any]]:
		if not self.device_store:
			return self.settings.devices.get("devices", [])
		return [
			{
				"device_id": item["device_id"],
				"server_url": self.settings.internal_device_ws_url,
				"os": item["platform"],
				"capabilities": item["capabilities"],
				"metadata": {
					"description": item["device_name"],
					"user_id": item["user_id"],
					"client_version": item["client_version"],
					"tips": WINDOWS_EXECUTION_GUIDANCE,
				},
				"auto_connect": True,
				"max_retries": 5,
			}
			for item in self.device_store.active()
		]

	async def _start_device_server(self) -> None:
		if self._port_open():
			return
		environment = os.environ.copy()
		environment["PYTHONPATH"] = str(self.settings.ufo_root)
		self.device_server_process = await asyncio.create_subprocess_exec(
			sys.executable,
			"-m",
			"ufo.server.app",
			"--host",
			self.settings.device_server_host,
			"--port",
			str(self.settings.device_server_port),
			"--api-key",
			self.settings.device_server_api_key,
			"--log-level",
			"WARNING",
			cwd=self.settings.ufo_root,
			env=environment,
			stdout=None,
			stderr=None,
		)
		for _ in range(40):
			if self.device_server_process.returncode is not None:
				raise RuntimeError("UFO device server stopped during startup")
			if self._port_open():
				return
			await asyncio.sleep(0.25)
		raise RuntimeError("UFO device server did not become ready")

	def _watch_device_server(self) -> None:
		path = f"/ws?token={quote(self.settings.device_server_api_key)}"
		if self.device_server_watchdog_stop.wait(15):
			return
		while not self.device_server_watchdog_stop.is_set():
			if not probe_websocket_server(
				self.settings.device_server_host,
				self.settings.device_server_port,
				path,
			):
				os._exit(75)
			self.device_server_watchdog_checks += 1
			if self.device_server_watchdog_stop.wait(30):
				return

	async def restart_device_server(
		self, failed_process: asyncio.subprocess.Process | None = None
	) -> None:
		async with self.device_server_lock:
			if (
				failed_process is not None
				and self.device_server_process is not failed_process
				and self._port_open()
			):
				return
			process = self.device_server_process
			if process:
				try:
					os.kill(process.pid, signal.SIGKILL)
				except ProcessLookupError:
					pass
				try:
					await asyncio.wait_for(process.wait(), timeout=5)
				except TimeoutError:
					pass
			for _ in range(50):
				if not self._port_open():
					break
				await asyncio.sleep(0.1)
			else:
				raise RuntimeError("UFO device server did not stop")
			self.device_server_process = None
			await self._start_device_server()

	def _port_open(self) -> bool:
		try:
			with socket.create_connection(
				(self.settings.device_server_host, self.settings.device_server_port), timeout=0.2
			):
				return True
		except OSError:
			return False

	async def refresh_devices(self) -> None:
		configure_ufo(self.settings, self._device_config())
		self.devices_dirty = True
		if self.current_run_id is None:
			await self._reload_client()

	async def _reload_client(self) -> None:
		if self.client:
			await self.client.shutdown(force=True)
			self.client = None
		self.devices_dirty = False

	async def enqueue(self, run_id: str) -> None:
		await self.queue.put(run_id)

	async def stop(self, run_id: str) -> dict[str, Any]:
		run = self.store.request_stop(run_id)
		if not run:
			raise KeyError(run_id)
		if run["status"] == "queued":
			return self.store.update(
				run_id,
				status="stopped",
				progress=100,
				current_stage="任务已停止",
				completed_at=utc_now(),
			)
		if self.current_run_id == run_id and self.client:
			await self.client.shutdown(force=True)
			self.client = None
		return self.store.update(run_id, current_stage="正在停止任务")

	async def _ensure_client(self):
		if self.devices_dirty and self.current_run_id is None:
			await self._reload_client()
		if self.client is not None:
			return self.client
		from config.config_loader import get_galaxy_config
		from galaxy.core.events import get_event_bus
		from galaxy.galaxy_client import GalaxyClient

		patch_constellation_heartbeat()
		get_galaxy_config(reload=True)
		self.client = GalaxyClient(max_rounds=self.settings.max_rounds, log_level="WARNING")
		await self.client.initialize()
		if not self.observer_subscribed:
			get_event_bus().subscribe(self.observer)
			self.observer_subscribed = True
		return self.client

	async def _worker(self) -> None:
		while True:
			run_id = await self.queue.get()
			try:
				await self._execute(run_id)
			except asyncio.CancelledError:
				raise
			except Exception as exc:
				self.store.update(
					run_id,
					status="failed",
					progress=100,
					current_stage="UFO3 运行失败",
					error=str(exc),
					completed_at=utc_now(),
				)
			finally:
				self.current_run_id = None
				self.observer.run_id = None
				if self.devices_dirty:
					await self._reload_client()
				self.queue.task_done()

	async def _execute(self, run_id: str) -> None:
		run = self.store.get(run_id)
		if not run or run["status"] == "stopped" or run["stop_requested"]:
			return
		started_at = utc_now()
		started_clock = time.monotonic()
		self.current_run_id = run_id
		self.observer.run_id = run_id
		self.observer.count = 0
		self.store.update(
			run_id,
			status="running",
			progress=4,
			current_stage="正在初始化 UFO3",
			started_at=started_at,
			ufo_commit=self.commit,
			model=self.settings.qwen_model,
		)
		client = await self._ensure_client()
		client.session_name = f"ione_{run['session_id']}"
		result = await client.process_request(build_prompt(run["request"], run["history"]))
		elapsed = time.monotonic() - started_clock
		latest = self.store.get(run_id)
		if latest["stop_requested"] or result.get("status") == "stopped":
			self.store.update(
				run_id,
				status="stopped",
				progress=100,
				current_stage="任务已停止",
				completed_at=utc_now(),
				elapsed_seconds=elapsed,
			)
			return
		if result_failed(result):
			self.store.update(
				run_id,
				status="failed",
				progress=100,
				current_stage="UFO3 执行失败",
				error=failure_message(result),
				completed_at=utc_now(),
				elapsed_seconds=elapsed,
			)
			return
		answer = extract_answer(result, latest["events"])
		if not answer:
			answer = "UFO3 已完成执行。请查看执行事件和已连接设备上的任务结果。"
		self.store.update(
			run_id,
			status="completed",
			progress=100,
			current_stage="任务已完成",
			answer=answer,
			completed_at=utc_now(),
			elapsed_seconds=elapsed,
		)
