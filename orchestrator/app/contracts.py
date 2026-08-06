from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator

GRAPH_VERSION = "lead-agent-v1"


class RiskLevel(str, Enum):
	READ = "read"
	LOW_WRITE = "low_write"
	HIGH_WRITE = "high_write"


class AgentToolCall(BaseModel):
	id: str = Field(min_length=1, max_length=120)
	name: str = Field(min_length=1, max_length=120)
	arguments: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
	type: Literal["tool_call", "answer"]
	tool_call: AgentToolCall | None = None
	content: str = ""
	reason: str = ""

	@model_validator(mode="after")
	def validate_payload(self) -> AgentDecision:
		if self.type == "tool_call" and not self.tool_call:
			raise ValueError("tool_call is required when type is tool_call")
		if self.type == "answer" and not self.content.strip():
			raise ValueError("content is required when type is answer")
		return self


class ToolOutcome(BaseModel):
	ok: bool
	tool: str
	execution_id: str
	data: dict[str, Any] = Field(default_factory=dict)
	evidence: list[dict[str, Any]] = Field(default_factory=list)
	error: str | None = None
	retryable: bool = False
	idempotent_replay: bool = False


@dataclass(frozen=True)
class ToolSpec:
	name: str
	version: str
	description: str
	argument_model: type[BaseModel]
	risk_level: RiskLevel = RiskLevel.READ
	required_roles: frozenset[str] = frozenset()
	max_attempts: int = 1
	idempotent: bool = True

	def public_definition(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"version": self.version,
			"description": self.description,
			"risk_level": self.risk_level.value,
			"arguments": self.argument_model.model_json_schema(),
		}


class PolicyResult(BaseModel):
	allowed: bool
	requires_approval: bool = False
	reason: str = ""


class LeadAgentState(TypedDict, total=False):
	run_id: str
	thread_id: str
	graph_version: str
	request: str
	user_id: str
	tenant: str
	roles: list[str]
	profile: dict[str, Any]
	sources: list[dict[str, Any]]
	messages: list[dict[str, Any]]
	intent: dict[str, Any]
	plan: list[str]
	completed_tools: list[str]
	tool_results: list[dict[str, Any]]
	pending_tool_call: dict[str, Any]
	criteria: dict[str, Any]
	raw_candidates: list[dict[str, Any]]
	candidates: list[dict[str, Any]]
	evidence: list[dict[str, Any]]
	artifacts: list[dict[str, Any]]
	risk_level: str
	iteration_count: int
	no_progress_count: int
	started_at: str
	deadline_at: str
	status: str
	errors: list[dict[str, Any]]
	summary: str
	final_answer: str
	partial: bool


def idempotency_key(run_id: str, tool_call: AgentToolCall, version: str) -> str:
	payload = json.dumps(
		{
			"run_id": run_id,
			"tool": tool_call.name,
			"version": version,
			"arguments": tool_call.arguments,
		},
		ensure_ascii=False,
		sort_keys=True,
		separators=(",", ":"),
	)
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()
