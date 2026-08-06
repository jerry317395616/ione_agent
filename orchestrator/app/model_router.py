from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.clients import QwenClient
from app.contracts import AgentDecision, AgentToolCall, LeadAgentState
from app.settings import Settings


class ModelRouter:
	"""Use Qwen as the stable execution controller after the planning node."""

	def __init__(self, settings: Settings, qwen: QwenClient) -> None:
		self.settings = settings
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
		instructions = (
			"你是 I-ONE Agent 的生产级执行控制模型。DeepSeek 已经完成首次规划，"
			"你只负责根据当前状态决定下一步，不执行工具。"
			"每次只允许调用一个白名单工具。只输出 JSON，不要 Markdown。"
			"调用工具时输出 {\"type\":\"tool_call\",\"tool_call\":{\"id\":\"call_x\","
			"\"name\":\"工具名\",\"arguments\":{}} ,\"reason\":\"简短原因\"}。"
			"任务完成时输出 {\"type\":\"answer\",\"content\":\"最终回答\",\"reason\":\"\"}。"
			"只能从 eligible_tools 中选择工具，不得输出未注册工具，不得生成代码、Shell、SQL 或网址调用。"
			"eligible_tools 非空时不得提前回答；为空时返回最终回答。"
		)
		payload = {
			"state": self._context(state),
			"eligible_tools": eligible_tools,
			"available_tools": tools,
		}
		user = json.dumps(payload, ensure_ascii=False)
		raw: Any = {}
		try:
			raw = self.qwen.json(
				instructions,
				user,
				{},
				timeout=60,
				max_attempts=1,
				run_id=state.get("run_id"),
				purpose="agent_control",
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
