from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.clients import QwenClient
from app.models import ClassifyRequest, CreateRunRequest
from app.settings import Settings
from app.store import RunStore, utc_now
from app.workflow import LeadWorkflow, Stopped

settings = Settings.from_environment()
settings.data_dir.mkdir(parents=True, exist_ok=True)
store = RunStore(settings.data_dir / "runs.sqlite3")
workflow = LeadWorkflow(settings, store)
queue: asyncio.Queue[str] = asyncio.Queue()
workers: list[asyncio.Task] = []


def authorize(authorization: str | None = Header(default=None)) -> None:
	expected = f"Bearer {settings.api_token}"
	if not authorization or not hmac.compare_digest(authorization, expected):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


async def execute(run_id: str) -> None:
	run = store.get(run_id)
	if not run or run["stop_requested"]:
		return
	started = monotonic()
	store.update(run_id, status="running", stage="parsing", started_at=run.get("started_at") or utc_now())
	try:
		state = await asyncio.to_thread(workflow.run, run_id, run["payload"])
		partial = bool(state.get("partial"))
		result = {
			"criteria": state.get("criteria") or {},
			"candidates": state.get("candidates") or [],
			"summary": state.get("summary") or "AI 获客任务已完成。",
		}
		store.update(
			run_id,
			status="completed",
			stage="partial" if partial else "completed",
			progress=100,
			current_stage="部分完成，等待 Frappe 入库" if partial else "分析完成，等待 Frappe 入库",
			result=result,
			completed_at=utc_now(),
			elapsed_seconds=round(monotonic() - started, 3),
		)
	except Stopped:
		store.update(
			run_id,
			status="stopped",
			stage="stopped",
			current_stage="任务已停止",
			completed_at=utc_now(),
			elapsed_seconds=round(monotonic() - started, 3),
		)
	except Exception as exc:
		store.update(
			run_id,
			status="failed",
			stage="failed",
			current_stage="执行失败",
			error=f"{type(exc).__name__}: {exc}",
			completed_at=utc_now(),
			elapsed_seconds=round(monotonic() - started, 3),
		)


async def worker() -> None:
	while True:
		run_id = await queue.get()
		try:
			await execute(run_id)
		finally:
			queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
	for run_id in store.recoverable():
		queue.put_nowait(run_id)
	for index in range(settings.max_concurrent_runs):
		workers.append(asyncio.create_task(worker(), name=f"lead-worker-{index + 1}"))
	yield
	for task in workers:
		task.cancel()
	await asyncio.gather(*workers, return_exceptions=True)


app = FastAPI(
	title="I-ONE Lead Intelligence Orchestrator",
	description="LangGraph orchestration for verifiable lead discovery",
	version="0.1.0",
	lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
	return {
		"status": "healthy",
		"runtime": "LangGraph",
		"model": settings.qwen_model,
		"hermes": "configured" if settings.hermes_api_key else "unavailable",
		"deepseek": "configured" if settings.deepseek_token else "unavailable",
		"queued": queue.qsize(),
		"time": datetime.now(timezone.utc).isoformat(),
	}


@app.post("/v1/classify", dependencies=[Depends(authorize)])
def classify(request: ClassifyRequest) -> dict:
	message = request.message.strip()
	objects = ("线索", "招标", "投标", "采购公告", "商机", "获客")
	actions = ("找", "搜", "搜索", "收集", "整理", "发现", "分析", "监测")
	if any(word in message for word in objects) and any(word in message for word in actions):
		return {"intent": "lead_discovery", "confidence": 1.0}
	try:
		result = QwenClient(settings).json(
			"判断用户请求属于 lead_discovery（联网找招标、采购或销售线索）还是 desktop（其他桌面任务）。只输出 JSON。",
			f'{{"message": {message!r}, "schema": {{"intent": "lead_discovery|desktop", "confidence": "0-1"}}}}',
			{"intent": "desktop", "confidence": 0},
		)
		intent = result.get("intent") if isinstance(result, dict) else "desktop"
		return {"intent": intent if intent in {"lead_discovery", "desktop"} else "desktop", "confidence": result.get("confidence", 0)}
	except Exception:
		return {"intent": "desktop", "confidence": 0}


@app.post("/v1/runs", dependencies=[Depends(authorize)])
async def create_run(request: CreateRunRequest) -> dict:
	run = store.create(request.model_dump())
	if run["status"] == "queued":
		await queue.put(run["run_id"])
	return run


@app.get("/v1/runs/{run_id}", dependencies=[Depends(authorize)])
def get_run(run_id: str) -> dict:
	run = store.get(run_id)
	if not run:
		raise HTTPException(status_code=404, detail="Run not found")
	return run


@app.post("/v1/runs/{run_id}/stop", dependencies=[Depends(authorize)])
def stop_run(run_id: str) -> dict:
	run = store.request_stop(run_id)
	if not run:
		raise HTTPException(status_code=404, detail="Run not found")
	if run["status"] == "queued":
		run = store.update(run_id, status="stopped", stage="stopped", completed_at=utc_now())
	return run
