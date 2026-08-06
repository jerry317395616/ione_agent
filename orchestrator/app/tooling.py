from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.contracts import (
	AgentToolCall,
	LeadAgentState,
	ToolOutcome,
	ToolSpec,
	idempotency_key,
)
from app.policy import ToolPolicy
from app.store import RunStore


class ToolExecutionError(RuntimeError):
	pass


class ToolRegistry:
	def __init__(self) -> None:
		self._tools: dict[str, tuple[ToolSpec, Callable[[LeadAgentState, BaseModel], dict[str, Any]]]] = {}

	def register(
		self,
		spec: ToolSpec,
		handler: Callable[[LeadAgentState, BaseModel], dict[str, Any]],
	) -> None:
		if spec.name in self._tools:
			raise ValueError(f"Tool {spec.name} is already registered")
		self._tools[spec.name] = (spec, handler)

	def get(self, name: str) -> tuple[ToolSpec, Callable[[LeadAgentState, BaseModel], dict[str, Any]]]:
		try:
			return self._tools[name]
		except KeyError as exc:
			raise ToolExecutionError(f"工具 {name} 未注册或不在白名单中。") from exc

	def definitions(self) -> list[dict[str, Any]]:
		return [spec.public_definition() for spec, _handler in self._tools.values()]

	def names(self) -> list[str]:
		return list(self._tools)


class GovernedToolNode:
	def __init__(self, registry: ToolRegistry, policy: ToolPolicy, store: RunStore) -> None:
		self.registry = registry
		self.policy = policy
		self.store = store

	def __call__(self, state: LeadAgentState) -> dict[str, Any]:
		call = AgentToolCall.model_validate(state.get("pending_tool_call") or {})
		spec, handler = self.registry.get(call.name)
		policy = self.policy.evaluate(spec, roles=state.get("roles") or [])
		if not policy.allowed:
			raise ToolExecutionError(policy.reason)

		try:
			arguments = spec.argument_model.model_validate(call.arguments)
		except ValidationError as exc:
			raise ToolExecutionError(f"工具 {call.name} 参数校验失败：{exc}") from exc

		key = idempotency_key(state["run_id"], call, spec.version)
		existing = self.store.begin_tool_execution(
			run_id=state["run_id"],
			tool_call_id=call.id,
			tool_name=spec.name,
			tool_version=spec.version,
			risk_level=spec.risk_level.value,
			arguments=arguments.model_dump(mode="json"),
			idempotency_key=key,
		)
		if existing.get("status") == "completed":
			outcome = ToolOutcome.model_validate(existing["result"])
			outcome.idempotent_replay = True
			return self._state_update(state, outcome)

		last_error: Exception | None = None
		for attempt in range(1, max(1, spec.max_attempts) + 1):
			try:
				data = handler(state, arguments)
				outcome = ToolOutcome(
					ok=True,
					tool=spec.name,
					execution_id=existing["execution_id"],
					data=data,
					evidence=list(data.get("evidence") or []),
				)
				self.store.complete_tool_execution(key, outcome.model_dump(mode="json"))
				return self._state_update(state, outcome)
			except Exception as exc:
				last_error = exc
				if attempt < spec.max_attempts:
					time.sleep(min(4, 2 ** (attempt - 1)))

		message = f"{type(last_error).__name__}: {last_error}"
		outcome = ToolOutcome(
			ok=False,
			tool=spec.name,
			execution_id=existing["execution_id"],
			error=message,
			retryable=spec.idempotent,
		)
		self.store.fail_tool_execution(key, outcome.model_dump(mode="json"), message)
		raise ToolExecutionError(f"工具 {spec.name} 执行失败：{message}") from last_error

	@staticmethod
	def _state_update(state: LeadAgentState, outcome: ToolOutcome) -> dict[str, Any]:
		data = dict(outcome.data)
		completed = list(state.get("completed_tools") or [])
		if outcome.tool not in completed:
			completed.append(outcome.tool)
		tool_results = list(state.get("tool_results") or [])
		tool_results.append(
			{
				"tool": outcome.tool,
				"execution_id": outcome.execution_id,
				"ok": outcome.ok,
				"idempotent_replay": outcome.idempotent_replay,
			}
		)
		messages = list(state.get("messages") or [])
		messages.append(
			{
				"role": "tool",
				"tool_call_id": state.get("pending_tool_call", {}).get("id"),
				"name": outcome.tool,
				"content": "执行成功" if outcome.ok else (outcome.error or "执行失败"),
			}
		)
		return {
			**data,
			"completed_tools": completed,
			"tool_results": tool_results[-100:],
			"messages": messages[-60:],
			"pending_tool_call": {},
			"status": "running",
		}
