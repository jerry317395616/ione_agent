from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.clients import DeepSeekClient, QwenClient
from app.contracts import AgentDecision, AgentToolCall, LeadAgentState
from app.settings import Settings


class ModelRouter:
	"""Use DeepSeek tool calls for control and Qwen only as a fallback."""

	def __init__(self, settings: Settings, deepseek: DeepSeekClient, qwen: QwenClient) -> None:
		self.settings = settings
		self.deepseek = deepseek
		self.qwen = qwen

	@staticmethod
	def _normalize(payload: Any) -> dict[str, Any]:
		if not isinstance(payload, dict):
			return {}
		if payload.get("type") == "tool_call" and not payload.get("tool_call"):
			calls = payload.get("tool_calls") or []
			if isinstance(calls, list) and calls:
				payload = {**payload, "tool_call": calls[0]}
		call = payload.get("tool_call")
		if isinstance(call, dict):
			call = dict(call)
			call.setdefault("id", f"call_{uuid4().hex[:16]}")
			if "args" in call and "arguments" not in call:
				call["arguments"] = call.pop("args")
			payload = {**payload, "tool_call": call}
		return payload

	@staticmethod
	def _context(state: LeadAgentState) -> dict[str, Any]:
		criteria = state.get("criteria") or {}
		candidates = state.get("candidates") or []
		return {
			"run_id": state.get("run_id"),
			"request": state.get("request"),
			"intent": state.get("intent") or {},
			"goal": state.get("goal") or "",
			"plan": state.get("plan") or [],
			"planning_model": state.get("planning_model") or "",
			"completion_criteria": state.get("completion_criteria") or {},
			"completed_tools": state.get("completed_tools") or [],
			"iteration_count": state.get("iteration_count", 0),
			"criteria": criteria,
			"raw_candidate_count": len(state.get("raw_candidates") or []),
			"candidate_count": len(candidates),
			"qualified_count": sum(
				float(item.get("relevance_score") or 0) >= float(criteria.get("score_threshold") or 70)
				for item in candidates
			),
			"summary": state.get("summary") or "",
			"errors": (state.get("errors") or [])[-3:],
		}

	def decide(
		self,
		state: LeadAgentState,
		*,
		tools: list[dict[str, Any]],
		eligible_tools: list[str],
	) -> AgentDecision:
		if not eligible_tools:
			return AgentDecision(
				type="answer",
				content=state.get("summary") or "任务已完成。",
				reason="所有计划步骤已经完成。",
			)
		instructions = (
			"你是 I-ONE Agent 的生产级执行控制模型。根据已验证计划和当前状态选择下一项工具。"
			"必须调用且每次只调用一个提供的白名单工具，不得直接执行工具，不得生成代码、Shell、SQL，"
			"不得调用未提供的工具，也不得修改计划顺序或跳过依赖。"
		)
		payload = {
			"state": self._context(state),
			"eligible_tools": eligible_tools,
			"available_tools": tools,
		}
		user = json.dumps(payload, ensure_ascii=False)
		raw: Any = {}
		try:
			raw = self.deepseek.tool_decision(
				instructions,
				user,
				tools=tools,
				timeout=60,
				run_id=state.get("run_id"),
				purpose="agent_control",
			)
			return AgentDecision.model_validate(self._normalize(raw))
		except Exception:
			try:
				raw = self.qwen.json(
					"你是执行控制降级模型。只能从 eligible_tools 选择一项，且只输出 JSON。"
					"格式为 {\"type\":\"tool_call\",\"tool_call\":{\"name\":\"工具名\","
					"\"arguments\":{}},\"reason\":\"简短原因\"}。",
					user,
					{},
					timeout=60,
					max_attempts=1,
					run_id=state.get("run_id"),
					purpose="agent_control_fallback",
				)
			except Exception:
				raw = {}
		try:
			return AgentDecision.model_validate(self._normalize(raw))
		except ValidationError:
			if eligible_tools:
				return AgentDecision(
					type="tool_call",
					tool_call=AgentToolCall(
						id=f"call_{uuid4().hex[:16]}",
						name=eligible_tools[0],
						arguments={},
					),
					reason="控制模型输出未通过结构校验，按已验证计划继续。",
				)
			return AgentDecision(
				type="answer",
				content=state.get("summary") or "任务已完成。",
				reason="模型输出未通过结构校验，返回已保存的结构化结果。",
			)
