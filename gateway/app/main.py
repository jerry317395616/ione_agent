from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import quote

import httpx
import websockets
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.websockets import WebSocketState

from app.device_store import DeviceStore
from app.models import CreateRunRequest, RegisterDeviceRequest
from app.runtime import UFORuntime, git_commit
from app.settings import Settings
from app.store import RunStore

settings = Settings.from_environment()
settings.data_dir.mkdir(parents=True, exist_ok=True)
store = RunStore(settings.data_dir / "runs.sqlite3")
device_store = DeviceStore(settings.data_dir / "devices.sqlite3")
runtime = UFORuntime(settings, store, device_store)


def schedule_gateway_restart() -> None:
	asyncio.get_running_loop().call_later(0.25, os._exit, 75)


def authorize(authorization: str | None = Header(default=None)) -> None:
	expected = f"Bearer {settings.gateway_token}"
	if not authorization or not hmac.compare_digest(authorization, expected):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def authorize_device(authorization: str | None = Header(default=None)) -> dict:
	prefix = "Bearer "
	if not authorization or not authorization.startswith(prefix):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
	device = device_store.authenticate_token(authorization[len(prefix) :])
	if not device:
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
	return device


@asynccontextmanager
async def lifespan(app: FastAPI):
	await runtime.start()
	yield
	await runtime.close()


app = FastAPI(
	title="I-ONE UFO Gateway",
	description="Private execution gateway for I-ONE Agent and UFO3",
	version="0.1.0",
	lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
	return {
		"status": "healthy",
		"runtime": "UFO3",
		"ufo_branch": "main",
		"ufo_commit": git_commit(settings.ufo_root),
		"model": settings.qwen_model,
		"devices_configured": len(device_store.active()),
		"devices_online": sum(item["status"] == "online" for item in device_store.active()),
		"device_server_watchdog_checks": runtime.device_server_watchdog_checks,
	}


def public_device(device: dict) -> dict:
	return {key: value for key, value in device.items() if key != "token_hash"}


@app.post("/v1/devices", dependencies=[Depends(authorize)])
async def register_device(request: RegisterDeviceRequest) -> dict:
	device = device_store.register(request.model_dump())
	await runtime.refresh_devices()
	separator = "&" if "?" in settings.device_public_ws_url else "?"
	return {
		**public_device(device),
		"model": settings.qwen_model,
		"model_api_base": settings.device_model_api_base,
		"connection_url": (
			f"{settings.device_public_ws_url}{separator}device_id={quote(request.device_id)}"
			f"&token={quote(request.device_token)}"
		),
	}


@app.post("/device/openai/v1/chat/completions")
async def device_chat_completions(
	request: Request,
	device: Annotated[dict, Depends(authorize_device)],
) -> StreamingResponse:
	body = await request.body()
	client = httpx.AsyncClient(timeout=None)
	upstream_request = client.build_request(
		"POST",
		f"{settings.openai_base_url}/chat/completions",
		content=body,
		headers={
			"Authorization": f"Bearer {settings.qwen_api_key}",
			"Content-Type": request.headers.get("content-type", "application/json"),
			"X-I-ONE-Device-ID": device["device_id"],
		},
	)
	try:
		response = await client.send(upstream_request, stream=True)
	except Exception:
		await client.aclose()
		raise

	async def close_upstream() -> None:
		await response.aclose()
		await client.aclose()

	return StreamingResponse(
		response.aiter_raw(),
		status_code=response.status_code,
		media_type=response.headers.get("content-type", "application/json"),
		background=BackgroundTask(close_upstream),
	)


@app.get("/v1/devices", dependencies=[Depends(authorize)])
def list_devices() -> list[dict]:
	return [public_device(item) for item in device_store.list()]


@app.delete("/v1/devices/{device_id}", dependencies=[Depends(authorize)])
async def revoke_device(device_id: str) -> dict:
	device = device_store.revoke(device_id)
	if not device:
		raise HTTPException(status_code=404, detail="Device not found")
	await runtime.refresh_devices()
	return public_device(device)


@app.websocket("/device/ws")
async def device_websocket(websocket: WebSocket, device_id: str, token: str) -> None:
	if not device_store.authenticate(device_id, token):
		await websocket.close(code=1008, reason="Invalid or revoked device token")
		return
	await websocket.accept()
	upstream_url = (
		f"ws://{settings.device_server_host}:{settings.device_server_port}/ws"
		f"?token={quote(settings.device_server_api_key)}"
	)
	try:
		upstream = None
		failed_process = runtime.device_server_process
		for attempt in range(2):
			try:
				upstream = await websockets.connect(upstream_url, max_size=None, open_timeout=10)
				break
			except (TimeoutError, OSError):
				if attempt:
					schedule_gateway_restart()
					raise
				try:
					await runtime.restart_device_server(failed_process)
				except RuntimeError:
					schedule_gateway_restart()
					raise
		if upstream is None:
			raise RuntimeError("Unable to connect to the UFO device server")

		try:
			device_store.set_status(device_id, "online")

			async def to_upstream() -> None:
				while True:
					message = await websocket.receive()
					if message["type"] == "websocket.disconnect":
						break
					if message.get("text") is not None:
						await upstream.send(message["text"])
					elif message.get("bytes") is not None:
						await upstream.send(message["bytes"])

			async def to_device() -> None:
				async for message in upstream:
					if isinstance(message, bytes):
						await websocket.send_bytes(message)
					else:
						await websocket.send_text(message)

			tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_device())]
			done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
			for task in pending:
				task.cancel()
			for task in done:
				task.result()
		finally:
			try:
				await asyncio.wait_for(upstream.close(), timeout=2)
			except TimeoutError:
				pass
	except (TimeoutError, OSError, RuntimeError, WebSocketDisconnect, websockets.WebSocketException):
		pass
	finally:
		device_store.set_status(device_id, "offline")
		if websocket.client_state != WebSocketState.DISCONNECTED:
			await websocket.close(code=1013, reason="Device service is restarting")


@app.post("/v1/runs", dependencies=[Depends(authorize)])
async def create_run(request: CreateRunRequest) -> dict:
	run = store.create(request.model_dump(), model=settings.qwen_model, ufo_commit=runtime.commit)
	if run["status"] == "queued":
		await runtime.enqueue(run["run_id"])
	return run


@app.get("/v1/runs/{run_id}", dependencies=[Depends(authorize)])
def get_run(run_id: str) -> dict:
	run = store.get(run_id)
	if not run:
		raise HTTPException(status_code=404, detail="Run not found")
	return run


@app.post("/v1/runs/{run_id}/stop", dependencies=[Depends(authorize)])
async def stop_run(run_id: str) -> dict:
	try:
		return await runtime.stop(run_id)
	except KeyError as exc:
		raise HTTPException(status_code=404, detail="Run not found") from exc
