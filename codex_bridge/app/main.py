from __future__ import annotations

import hmac
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from app.app_server import AppServerError, CodexAppServer
from app.bridge import ChatCompletionRequest, CodexBridge
from app.settings import Settings

settings = Settings.from_environment()
settings.prepare()
app_server = CodexAppServer(settings)
bridge = CodexBridge(settings, app_server)


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
	title="I-ONE Codex App Server Bridge",
	description="LibreChat protocol bridge for Codex App Server and DeepSeek",
	version="1.0.0",
	lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
	return {
		"status": "healthy" if app_server.alive else "degraded",
		"agent_runtime": "codex-app-server",
		"model_provider": settings.model_provider,
		"model": settings.model,
		"business_orchestration": False,
		"other_agents": False,
		"app_server_generation": app_server.generation,
		"conversations": bridge.store.count(),
		"time": datetime.now(timezone.utc).isoformat(),
	}


@app.get("/ready")
async def ready() -> dict:
	if not app_server.alive:
		try:
			await app_server.start()
		except Exception as exc:
			raise HTTPException(status_code=503, detail=str(exc)) from exc
	return {"ready": True, "runtime": "codex-app-server"}


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
):
	user_id = (x_librechat_user_id or "librechat-user")[:140]
	conversation_id = (x_librechat_conversation_id or f"conversation-{uuid.uuid4().hex}")[:180]
	if request.stream:
		return StreamingResponse(
			bridge.stream(request, user_id=user_id, conversation_id=conversation_id),
			media_type="text/event-stream",
			headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
		)
	try:
		return await bridge.complete(request, user_id=user_id, conversation_id=conversation_id)
	except ValueError as exc:
		raise HTTPException(status_code=422, detail=str(exc)) from exc
	except AppServerError as exc:
		raise HTTPException(status_code=502, detail=str(exc)) from exc

