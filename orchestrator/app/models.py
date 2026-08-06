from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.contracts import GRAPH_VERSION


class ClassifyRequest(BaseModel):
	message: str = Field(min_length=1, max_length=12000)


class CreateRunRequest(BaseModel):
	client_run_id: str = Field(min_length=1, max_length=140)
	task_id: str = Field(min_length=1, max_length=140)
	user_id: str = Field(min_length=1, max_length=140)
	request: str = Field(min_length=1, max_length=12000)
	tenant: str = Field(default="manager.myyr.top", min_length=1, max_length=255)
	roles: list[str] = Field(default_factory=list, max_length=100)
	graph_version: str = Field(default=GRAPH_VERSION, min_length=1, max_length=80)
	profile: dict[str, Any] = Field(default_factory=dict)
	sources: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
