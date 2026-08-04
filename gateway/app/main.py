from __future__ import annotations

import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.models import CreateRunRequest
from app.runtime import UFORuntime, git_commit
from app.settings import Settings
from app.store import RunStore

settings = Settings.from_environment()
settings.data_dir.mkdir(parents=True, exist_ok=True)
store = RunStore(settings.data_dir / "runs.sqlite3")
runtime = UFORuntime(settings, store)


def authorize(authorization: str | None = Header(default=None)) -> None:
	expected = f"Bearer {settings.gateway_token}"
	if not authorization or not hmac.compare_digest(authorization, expected):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


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
		"devices_configured": len(settings.devices.get("devices", [])),
	}


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
