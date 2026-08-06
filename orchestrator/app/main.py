from __future__ import annotations

import asyncio
import hmac
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.clients import QwenClient
from app.contracts import GRAPH_VERSION
from app.librechat import ChatCompletionRequest, LibreChatBridge
from app.models import ClassifyRequest, CreateRunRequest
from app.settings import Settings
from app.store import RunStore, utc_now
from app.workflow import LeadWorkflow, Stopped

settings = Settings.from_environment()
settings.data_dir.mkdir(parents=True, exist_ok=True)
store = RunStore(settings.data_dir / "runs.sqlite3")
workflow = LeadWorkflow(settings, store)
librechat_bridge = LibreChatBridge(settings) if settings.librechat_ready else None
queue: asyncio.Queue[str] = asyncio.Queue()
workers: list[asyncio.Task] = []
enqueued_runs: set[str] = set()
enqueue_lock = asyncio.Lock()


def authorize(authorization: str | None = Header(default=None)) -> None:
	expected = f"Bearer {settings.api_token}"
	if not authorization or not hmac.compare_digest(authorization, expected):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


def authorize_librechat(authorization: str | None = Header(default=None)) -> None:
	expected = f"Bearer {settings.librechat_api_token}"
	if (
		not settings.librechat_ready
		or not authorization
		or not hmac.compare_digest(authorization, expected)
	):
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
			"intent": state.get("intent") or {},
			"goal": state.get("goal") or run["payload"].get("request"),
			"plan": state.get("plan") or [],
			"planning_model": state.get("planning_model") or "",
			"planning_error": state.get("planning_error") or "",
			"search_strategy": state.get("search_strategy") or {},
			"completion_criteria": state.get("completion_criteria") or {},
			"completion_evaluation": state.get("completion_evaluation") or {},
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
	if librechat_bridge:
		await librechat_bridge.close()
	workflow.close()


app = FastAPI(
	title="I-ONE Lead Intelligence Orchestrator",
	description="LangGraph orchestration for verifiable lead discovery",
	version="0.6.0",
	lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
	return {
		"status": "healthy",
		"runtime": "LangGraph",
		"graph_version": GRAPH_VERSION,
		"checkpoint_backend": workflow.checkpoint_backend,
		"planning_model": settings.deepseek_reasoning_model,
		"planning_fallback_model": "qwen",
		"control_model": settings.deepseek_fast_model,
		"analysis_model": settings.deepseek_fast_model,
		"fallback_model": settings.qwen_model,
		"model": settings.deepseek_reasoning_model,
		"hermes": "configured" if settings.hermes_api_key else "unavailable",
		"deepseek": workflow.deepseek.health() if settings.deepseek_ready else {"state": "unavailable"},
		"tools": workflow.registry.names(),
		"queued": queue.qsize(),
		"workers": len(workers),
		"librechat": "configured" if settings.librechat_ready else "unavailable",
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
		result = workflow.deepseek.json(
			"判断请求属于 lead_discovery（联网找招标、采购或销售线索）还是 desktop（其他桌面任务）。必须输出 json 对象。",
			f'{{"message": {message!r}, "schema": {{"intent": "lead_discovery|desktop", "confidence": "0-1"}}}}',
			{"intent": "desktop", "confidence": 0},
			model=settings.deepseek_fast_model,
			timeout=20,
			max_attempts=2,
			max_tokens=500,
			thinking=False,
			purpose="intent_classification",
		)
		intent = result.get("intent") if isinstance(result, dict) else "desktop"
		return {"intent": intent if intent in {"lead_discovery", "desktop"} else "desktop", "confidence": result.get("confidence", 0)}
	except Exception:
		try:
			result = QwenClient(settings).json(
				"判断用户请求属于 lead_discovery（联网找招标、采购或销售线索）还是 desktop（其他桌面任务）。只输出 JSON。",
				f'{{"message": {message!r}, "schema": {{"intent": "lead_discovery|desktop", "confidence": "0-1"}}}}',
				{"intent": "desktop", "confidence": 0},
				timeout=6,
				max_attempts=1,
				purpose="intent_classification_fallback",
			)
			intent = result.get("intent") if isinstance(result, dict) else "desktop"
			return {
				"intent": intent if intent in {"lead_discovery", "desktop"} else "desktop",
				"confidence": result.get("confidence", 0),
			}
		except Exception:
			return {"intent": "desktop", "confidence": 0}


@app.post("/v1/runs", dependencies=[Depends(authorize)])
async def create_run(request: CreateRunRequest) -> dict:
	payload = request.model_dump()
	payload["graph_version"] = GRAPH_VERSION
	run = store.create(payload)
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


@app.get("/v1/models", dependencies=[Depends(authorize_librechat)])
def list_openai_models() -> dict:
	return {
		"object": "list",
		"data": [
			{
				"id": "ione-agent",
				"object": "model",
				"created": 0,
				"owned_by": "I-ONE",
			}
		],
	}


@app.post("/v1/chat/completions", dependencies=[Depends(authorize_librechat)])
async def openai_chat_completions(
	request: ChatCompletionRequest,
	x_librechat_user_id: str | None = Header(default=None),
	x_librechat_conversation_id: str | None = Header(default=None),
):
	if not librechat_bridge:
		raise HTTPException(status_code=503, detail="LibreChat bridge is not configured")
	user_id = (x_librechat_user_id or "librechat-user")[:140]
	conversation_id = (x_librechat_conversation_id or f"conversation-{uuid.uuid4().hex}")[:180]
	if request.stream:
		return StreamingResponse(
			librechat_bridge.stream(
				request,
				user_id=user_id,
				conversation_id=conversation_id,
			),
			media_type="text/event-stream",
			headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
		)
	try:
		return await librechat_bridge.complete(
			request,
			user_id=user_id,
			conversation_id=conversation_id,
		)
	except ValueError as exc:
		raise HTTPException(status_code=422, detail=str(exc)) from exc
