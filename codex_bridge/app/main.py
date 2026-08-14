from __future__ import annotations

import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.app_server import AppServerError, CodexAppServer
from app.bridge import ChatCompletionRequest, CodexBridge
from app.public_output import public_error_message, sanitize_public_text
from app.settings import Settings

logger = logging.getLogger(__name__)

settings = Settings.from_environment()
settings.prepare()
app_server = CodexAppServer(settings)
bridge = CodexBridge(settings, app_server)


class LearningReviewRequest(BaseModel):
	decision: str
	evaluation_status: str
	reviewer: str
	note: str = ""


def authorize(authorization: str | None = Header(default=None)) -> None:
	expected = f"Bearer {settings.bridge_token}"
	if not authorization or not hmac.compare_digest(authorization, expected):
		raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@asynccontextmanager
async def lifespan(_: FastAPI):
	await app_server.start()
	yield
	bridge.close()
	await app_server.stop()


app = FastAPI(
	title="I-ONE Agent Service",
	description="Managed enterprise intelligence service for I-ONE Agent",
	version="1.0.0",
	lifespan=lifespan,
	docs_url=None,
	redoc_url=None,
	openapi_url=None,
)


@app.get("/health")
def health() -> dict:
	return {
		"status": "healthy" if app_server.alive else "degraded",
		"service": "I-ONE Agent",
		"time": datetime.now(timezone.utc).isoformat(),
	}


@app.get("/internal/health", dependencies=[Depends(authorize)])
def internal_health() -> dict:
	return {
		"status": "healthy" if app_server.alive else "degraded",
		"runtime_generation": app_server.generation,
		"agent_runtime": settings.runtime_mode,
		"model_network_scope": "private" if settings.require_private_model else "development",
		"business_connector": settings.mcp_enabled,
		"business_skills": settings.bundled_skills_dir.is_dir(),
		"conversations": bridge.store.count(),
		"time": datetime.now(timezone.utc).isoformat(),
	}


@app.get("/internal/learning/proposals", dependencies=[Depends(authorize)])
def learning_proposals(
	proposal_status: str = Query(default="pending", alias="status"),
	limit: int = Query(default=100, ge=1, le=500),
) -> dict:
	proxy = app_server.dynamic_tool_proxy
	if not proxy:
		raise HTTPException(status_code=503, detail="Learning service is unavailable")
	try:
		items = proxy.learning_store.list_proposals(status=proposal_status, limit=limit)
	except ValueError as exc:
		raise HTTPException(status_code=422, detail=str(exc)) from exc
	return {"status": proposal_status, "count": len(items), "items": items}


@app.post("/internal/learning/proposals/{proposal_id}/review", dependencies=[Depends(authorize)])
def review_learning_proposal(proposal_id: str, request: LearningReviewRequest) -> dict:
	proxy = app_server.dynamic_tool_proxy
	if not proxy:
		raise HTTPException(status_code=503, detail="Learning service is unavailable")
	try:
		return proxy.learning_store.review(
			proposal_id,
			decision=request.decision,
			reviewer=request.reviewer,
			evaluation_status=request.evaluation_status,
			note=request.note,
		)
	except ValueError as exc:
		raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/ready")
async def ready() -> dict:
	if not app_server.alive:
		try:
			await app_server.start()
		except Exception as exc:
			logger.exception("I-ONE Agent readiness probe failed")
			raise HTTPException(status_code=503, detail=public_error_message()) from exc
	return {"ready": True, "service": "I-ONE Agent"}


@app.get("/v1/models", dependencies=[Depends(authorize)])
def models() -> dict:
	return {
		"object": "list",
		"data": [{"id": "ione-agent", "object": "model", "created": 0, "owned_by": "I-ONE"}],
	}


@app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
async def chat_completions(
	request: ChatCompletionRequest,
	x_librechat_user_id: str | None = Header(default=None),
	x_librechat_conversation_id: str | None = Header(default=None),
	x_i_one_manager_user_email: str | None = Header(default=None, alias="X-I-ONE-Manager-User-Email"),
	x_i_one_manager_user_name: str | None = Header(default=None, alias="X-I-ONE-Manager-User-Name"),
	x_i_one_manager_username: str | None = Header(default=None, alias="X-I-ONE-Manager-Username"),
):
	user_id = (x_librechat_user_id or "librechat-user")[:140]
	conversation_id = (x_librechat_conversation_id or f"conversation-{uuid.uuid4().hex}")[:180]
	if request.stream:
		return StreamingResponse(
			bridge.stream(
				request,
				user_id=user_id,
				conversation_id=conversation_id,
				manager_user_email=x_i_one_manager_user_email,
				manager_user_hint=x_i_one_manager_user_name or x_i_one_manager_username,
			),
			media_type="text/event-stream",
			headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
		)
	try:
		return await bridge.complete(
			request,
			user_id=user_id,
			conversation_id=conversation_id,
			manager_user_email=x_i_one_manager_user_email,
			manager_user_hint=x_i_one_manager_user_name or x_i_one_manager_username,
		)
	except ValueError as exc:
		raise HTTPException(status_code=422, detail=sanitize_public_text(exc)) from exc
	except AppServerError as exc:
		reference = uuid.uuid4().hex[:10].upper()
		logger.exception("I-ONE Agent request failed reference=%s", reference)
		raise HTTPException(status_code=502, detail=public_error_message(reference)) from exc
