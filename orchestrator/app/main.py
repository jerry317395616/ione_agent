from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import PlainTextResponse

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
enqueued_runs: set[str] = set()
enqueue_lock = asyncio.Lock()


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
		partial = bool(state.get("partial")) or state.get("status") == "partial"
		result = {
			"criteria": state.get("criteria") or {},
			"candidates": state.get("candidates") or [],
			"summary": state.get("summary") or "AI 获客任务已完成。",
			"final_answer": state.get("final_answer") or state.get("summary") or "AI 获客任务已完成。",
			"graph_version": state.get("graph_version") or run.get("graph_version"),
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
			last_checkpoint_at=utc_now(),
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
			async with enqueue_lock:
				enqueued_runs.discard(run_id)
			queue.task_done()


async def enqueue(run_id: str) -> bool:
	async with enqueue_lock:
		if run_id in enqueued_runs:
			return False
		enqueued_runs.add(run_id)
	await queue.put(run_id)
	return True


@asynccontextmanager
async def lifespan(app: FastAPI):
	for run_id in store.recoverable():
		await enqueue(run_id)
	for index in range(settings.max_concurrent_runs):
		workers.append(asyncio.create_task(worker(), name=f"lead-worker-{index + 1}"))
	yield
	for task in workers:
		task.cancel()
	await asyncio.gather(*workers, return_exceptions=True)
	workflow.close()


app = FastAPI(
	title="I-ONE Lead Intelligence Orchestrator",
	description="LangGraph orchestration for verifiable lead discovery",
	version="0.4.0",
	lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
	return {
		"status": "healthy",
		"runtime": "LangGraph",
		"graph_version": "lead-agent-v1",
		"checkpoint_backend": workflow.checkpoint_backend,
		"control_model": settings.agent_control_model,
		"model": settings.qwen_model,
		"hermes": "configured" if settings.hermes_api_key else "unavailable",
		"deepseek": workflow.deepseek.health() if settings.deepseek_token else {"state": "unavailable"},
		"tools": workflow.registry.names(),
		"queued": queue.qsize(),
		"workers": len(workers),
		"time": datetime.now(timezone.utc).isoformat(),
	}


@app.get("/ready")
def ready() -> dict:
	return {
		"ready": True,
		"data_store": "sqlite-wal",
		"checkpoint_store": workflow.checkpoint_backend,
		"queue_capacity": settings.max_concurrent_runs,
	}


@app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(authorize)])
def metrics() -> str:
	values = {**store.metrics(), "queue_depth": queue.qsize(), "workers": len(workers)}
	return "\n".join(f"ione_agent_{key} {value}" for key, value in values.items()) + "\n"


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
		await enqueue(run["run_id"])
	return run


@app.get("/v1/runs/{run_id}", dependencies=[Depends(authorize)])
def get_run(run_id: str) -> dict:
	run = store.get(run_id)
	if not run:
		raise HTTPException(status_code=404, detail="Run not found")
	return run


@app.get("/v1/runs/{run_id}/trace", dependencies=[Depends(authorize)])
def get_run_trace(run_id: str) -> dict:
	if not store.get(run_id):
		raise HTTPException(status_code=404, detail="Run not found")
	return store.trace(run_id)


@app.post("/v1/runs/{run_id}/stop", dependencies=[Depends(authorize)])
def stop_run(run_id: str) -> dict:
	run = store.request_stop(run_id)
	if not run:
		raise HTTPException(status_code=404, detail="Run not found")
	if run["status"] == "queued":
		run = store.update(run_id, status="stopped", stage="stopped", completed_at=utc_now())
	return run
