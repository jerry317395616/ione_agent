from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from app.checkpointing import CheckpointManager
from app.clients import DeepSeekClient, HermesClient, QwenClient, SearxngClient, WebPageExtractor
from app.contracts import (
	GRAPH_VERSION,
	AgentDecision,
	AgentToolCall,
	LeadAgentState,
	RiskLevel,
	ToolSpec,
)
from app.model_router import ModelRouter
from app.policy import ToolPolicy
from app.search_queries import build_search_queries
from app.settings import Settings
from app.store import RunStore, utc_now
from app.tooling import GovernedToolNode, ToolRegistry


class ToolArguments(BaseModel):
	model_config = ConfigDict(extra="forbid")


class ParseLeadRequestArguments(ToolArguments):
	request: str | None = Field(default=None, max_length=12000)


class SearchPublicTendersArguments(ToolArguments):
	maximum_results: int | None = Field(default=None, ge=1, le=100)


class AnalyzeCandidatesArguments(ToolArguments):
	score_threshold: float | None = Field(default=None, ge=0, le=100)


class ReviewCandidatesArguments(ToolArguments):
	maximum_candidates: int = Field(default=20, ge=1, le=30)


class CompleteDiscoveryArguments(ToolArguments):
	pass


class Stopped(RuntimeError):
	pass


class LeadWorkflow:
	TOOL_SEQUENCE = (
		"parse_lead_request",
		"search_public_tenders",
		"analyze_lead_candidates",
		"review_qualified_leads",
		"complete_lead_discovery",
	)

	def __init__(self, settings: Settings, store: RunStore) -> None:
		self.settings = settings
		self.store = store
		self.qwen = QwenClient(settings, audit=store.record_model_call)
		self.hermes = HermesClient(settings)
		self.searxng = SearxngClient(settings)
		self.extractor = WebPageExtractor(settings)
		self.deepseek = DeepSeekClient(settings, audit=store.record_model_call)
		self.router = ModelRouter(settings, self.qwen, self.deepseek)
		self.registry = self._build_registry()
		self.tool_node = GovernedToolNode(self.registry, ToolPolicy(), store)

		builder = StateGraph(LeadAgentState)
		builder.add_node("initialize", self.initialize)
		builder.add_node("model", self.model)
		builder.add_node("tools", self.tool_node)
		builder.add_node("finalize", self.finalize)
		builder.add_edge(START, "initialize")
		builder.add_edge("initialize", "model")
		builder.add_conditional_edges("model", self.route_model, {"tools": "tools", "finalize": "finalize"})
		builder.add_edge("tools", "model")
		builder.add_edge("finalize", END)

		self.checkpoint_manager = CheckpointManager(
			data_dir=settings.data_dir,
			database_url=settings.checkpoint_database_url,
		)
		self.checkpoint_backend = self.checkpoint_manager.backend
		self.checkpointer = self.checkpoint_manager.saver
		self.graph = builder.compile(checkpointer=self.checkpointer)

	def close(self) -> None:
		self.checkpoint_manager.close()

	def _build_registry(self) -> ToolRegistry:
		registry = ToolRegistry()
		registry.register(
			ToolSpec(
				name="parse_lead_request",
				version="1.0.0",
				description="把自然语言获客需求解析为行业、区域、关键词、时间范围和评分条件。",
				argument_model=ParseLeadRequestArguments,
				max_attempts=2,
			),
			self.parse_criteria,
		)
		registry.register(
			ToolSpec(
				name="search_public_tenders",
				version="1.0.0",
				description="搜索公开招标与采购信息，抓取原文并由 Hermes 核验来源。",
				argument_model=SearchPublicTendersArguments,
				max_attempts=2,
			),
			self.research,
		)
		registry.register(
			ToolSpec(
				name="analyze_lead_candidates",
				version="1.0.0",
				description="依据公开证据提取需求、评估相关度与可信度并去重。",
				argument_model=AnalyzeCandidatesArguments,
				max_attempts=2,
			),
			self.analyze,
		)
		registry.register(
			ToolSpec(
				name="review_qualified_leads",
				version="1.0.0",
				description="使用 DeepSeek 网页版复核高价值线索并生成售前跟进方案，失败时降级 Qwen。",
				argument_model=ReviewCandidatesArguments,
				max_attempts=1,
			),
			self.review,
		)
		registry.register(
			ToolSpec(
				name="complete_lead_discovery",
				version="1.0.0",
				description="汇总结构化结果并交给 Frappe 执行受控入库。",
				argument_model=CompleteDiscoveryArguments,
				risk_level=RiskLevel.READ,
			),
			self.finish,
		)
		return registry

	def ensure_running(self, run_id: str) -> None:
		run = self.store.get(run_id)
		if not run or run["stop_requested"]:
			raise Stopped("任务已停止")

	def initialize(self, state: LeadAgentState) -> dict[str, Any]:
		if state.get("graph_version"):
			return {}
		started = datetime.now(timezone.utc)
		self.store.stage(state["run_id"], "planning", 5, "LangGraph 正在建立受控执行计划")
		return {
			"thread_id": state["run_id"],
			"graph_version": GRAPH_VERSION,
			"messages": [{"role": "user", "content": state["request"]}],
			"intent": {"name": "lead_discovery", "confidence": 1.0},
			"plan": list(self.TOOL_SEQUENCE),
			"completed_tools": [],
			"tool_results": [],
			"evidence": [],
			"artifacts": [],
			"risk_level": RiskLevel.READ.value,
			"iteration_count": 0,
			"no_progress_count": 0,
			"started_at": started.isoformat(),
			"deadline_at": (started + timedelta(seconds=self.settings.agent_run_budget_seconds)).isoformat(),
			"status": "running",
			"errors": [],
			"partial": False,
		}

	def _required_tool(self, state: LeadAgentState) -> str | None:
		completed = set(state.get("completed_tools") or [])
		return next((name for name in self.TOOL_SEQUENCE if name not in completed), None)

	def _default_arguments(self, name: str, state: LeadAgentState) -> dict[str, Any]:
		if name == "parse_lead_request":
			return {"request": state.get("request")}
		if name == "search_public_tenders":
			return {"maximum_results": (state.get("criteria") or {}).get("maximum_results")}
		if name == "analyze_lead_candidates":
			return {"score_threshold": (state.get("criteria") or {}).get("score_threshold")}
		if name == "review_qualified_leads":
			return {"maximum_candidates": 20}
		return {}

	def _budget_exhausted(self, state: LeadAgentState) -> bool:
		if int(state.get("iteration_count") or 0) >= self.settings.max_agent_iterations:
			return True
		deadline = state.get("deadline_at")
		return bool(deadline and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline))

	def model(self, state: LeadAgentState) -> dict[str, Any]:
		self.ensure_running(state["run_id"])
		required = self._required_tool(state)
		if self._budget_exhausted(state):
			summary = state.get("summary") or self._summary(state)
			return {
				"pending_tool_call": {},
				"summary": summary,
				"final_answer": f"{summary} 执行预算已用完，现有结果已安全保存，可从检查点继续。",
				"partial": True,
				"status": "partial",
			}

		decision = self.router.decide(
			state,
			tools=self.registry.definitions(),
			required_tool=required,
		)
		decision = self._guard_decision(decision, required, state)
		iteration = int(state.get("iteration_count") or 0) + 1
		self.store.update(
			state["run_id"],
			iteration_count=iteration,
			last_checkpoint_at=utc_now(),
		)
		if decision.type == "answer":
			return {
				"pending_tool_call": {},
				"final_answer": decision.content,
				"iteration_count": iteration,
				"status": "completed",
			}

		call = decision.tool_call
		assert call is not None
		messages = list(state.get("messages") or [])
		messages.append(
			{
				"role": "assistant",
				"content": decision.reason,
				"tool_calls": [call.model_dump(mode="json")],
			}
		)
		self.store.stage(
			state["run_id"],
			"tool_planning",
			min(90, 8 + iteration * 5),
			f"控制模型已选择工具：{call.name}",
		)
		return {
			"pending_tool_call": call.model_dump(mode="json"),
			"messages": messages[-60:],
			"iteration_count": iteration,
			"status": "running",
		}

	def _guard_decision(
		self,
		decision: AgentDecision,
		required_tool: str | None,
		state: LeadAgentState,
	) -> AgentDecision:
		if not required_tool:
			if decision.type == "answer":
				return decision
			return AgentDecision(
				type="answer",
				content=state.get("summary") or self._summary(state),
				reason="所有计划步骤已经完成。",
			)

		call = decision.tool_call
		if decision.type != "tool_call" or not call or call.name != required_tool:
			call = AgentToolCall(
				id=f"call_{uuid4().hex[:16]}",
				name=required_tool,
				arguments=self._default_arguments(required_tool, state),
			)
			return AgentDecision(
				type="tool_call",
				tool_call=call,
				reason="策略守卫按可恢复计划选择下一项必要工具。",
			)
		defaults = self._default_arguments(required_tool, state)
		call.arguments = {**defaults, **call.arguments}
		return decision

	@staticmethod
	def route_model(state: LeadAgentState) -> str:
		return "tools" if state.get("pending_tool_call") else "finalize"

	def parse_criteria(
		self,
		state: LeadAgentState,
		arguments: ParseLeadRequestArguments,
	) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		self.store.stage(run_id, "parsing", 12, "Qwen 正在理解获客目标", qwen="正在解析")
		profile = state.get("profile") or {}
		criteria = self.qwen.json(
			"你是企业获客任务解析器。只输出一个 JSON 对象，不要解释。未知信息使用 null，不得编造。",
			json.dumps(
				{
					"today": date.today().isoformat(),
					"request": arguments.request or state["request"],
					"saved_profile": profile,
					"required_schema": {
						"industry": "string",
						"regions": ["string"],
						"keywords": ["string"],
						"excluded_keywords": ["string"],
						"days_back": "integer",
						"minimum_budget": "number",
						"maximum_results": "integer",
						"score_threshold": "number",
					},
				},
				ensure_ascii=False,
			),
			{},
			run_id=run_id,
			purpose="parse_lead_criteria",
		)
		criteria = {**profile, **(criteria if isinstance(criteria, dict) else {})}
		criteria["maximum_results"] = max(1, min(100, int(criteria.get("maximum_results") or 30)))
		criteria["score_threshold"] = max(0, min(100, float(criteria.get("score_threshold") or 70)))
		return {"criteria": criteria}

	def research(
		self,
		state: LeadAgentState,
		arguments: SearchPublicTendersArguments,
	) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		criteria = dict(state["criteria"])
		if arguments.maximum_results:
			criteria["maximum_results"] = arguments.maximum_results
		self.store.stage(run_id, "researching", 30, "正在检索近期公开招标信息", hermes="等待核验", qwen="已完成")
		sources = state.get("sources") or []
		queries = build_search_queries(criteria, sources)
		search_limit = min(80, max(20, int(criteria["maximum_results"]) * 3))
		raw = self.searxng.search(queries, limit=search_limit)
		self.store.stage(run_id, "researching", 38, f"已发现 {len(raw)} 条公开信息，正在抓取原文证据")
		raw = self.extractor.enrich(raw, limit=min(12, int(criteria["maximum_results"])))
		self.ensure_running(run_id)
		hermes_limit = min(12, int(criteria["maximum_results"]))
		hermes_materials = [
			{key: value for key, value in item.items() if key != "raw_text"}
			| {"raw_text": str(item.get("raw_text") or "")[:6000]}
			for item in raw[:hermes_limit]
		]
		prompt = (
			"你是 I-ONE 行业情报研究员。以下素材已由受控采集器联网搜索并抓取原文。"
			"禁止继续调用搜索、浏览器、终端或其他工具，只能根据给定素材完成核验和整理。"
			"优先保留官方来源，不得编造项目、预算、联系人或网址。"
			"只返回 JSON 数组。每项字段：title,project_number,purchaser,agency,contact_name,"
			"contact_phone,contact_email,source_name,source_url,published_at,deadline,budget,region,"
			"industry,procurement_method,raw_text,evidence。evidence 是包含 url、snippet 的数组。\n"
			f"任务：{state['request']}\n条件：{json.dumps(criteria, ensure_ascii=False)}\n"
			f"优先来源：{json.dumps(sources, ensure_ascii=False)}\n"
			f"已采集素材：{json.dumps(hermes_materials, ensure_ascii=False)}"
		)
		partial = bool(state.get("partial"))
		if raw:
			self.store.stage(run_id, "researching", 48, "Hermes 正在核验来源并整理招标事实", hermes="正在核验")
			try:
				researched = self.hermes.research(prompt)
				if researched:
					by_url = {str(item.get("source_url") or ""): item for item in raw}
					raw = [
						{
							**by_url.get(str(item.get("source_url") or ""), {}),
							**item,
							"evidence": item.get("evidence")
							or by_url.get(str(item.get("source_url") or ""), {}).get("evidence")
							or [],
						}
						for item in researched
					]
				else:
					partial = True
			except Exception:
				partial = True
		else:
			partial = True
		self.store.stage(
			run_id,
			"researching",
			52,
			f"已获取 {len(raw)} 条公开信息，准备结构化分析",
			hermes="已完成" if not partial else "已保留采集结果，等待人工复核",
		)
		evidence = [entry for item in raw for entry in (item.get("evidence") or [])][:200]
		return {
			"criteria": criteria,
			"raw_candidates": raw[: criteria["maximum_results"]],
			"evidence": evidence,
			"partial": partial,
		}

	def analyze(
		self,
		state: LeadAgentState,
		arguments: AnalyzeCandidatesArguments,
	) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		criteria = dict(state["criteria"])
		if arguments.score_threshold is not None:
			criteria["score_threshold"] = arguments.score_threshold
		self.store.stage(run_id, "analyzing", 55, "Qwen 正在提取需求、评分并去重", qwen="正在分析")
		raw = state.get("raw_candidates") or []
		if not raw:
			return {"criteria": criteria, "candidates": []}
		compact = [
			{key: value for key, value in item.items() if key != "raw_text"}
			| {"raw_text": str(item.get("raw_text") or "")[:4000]}
			for item in raw
		]
		analyzed = self.qwen.json(
			"你是严谨的招标线索分析师。只使用输入证据，禁止补造。只输出 JSON 数组。",
			json.dumps(
				{
					"criteria": criteria,
					"candidates": compact,
					"instructions": (
						"保留输入字段，并新增 relevance_score(0-100)、confidence(0-100)、risk_level(低/中/高)、"
						"requirement_summary、qualification_requirements、recommendation。来源网址为空的记录必须删除；"
						"明显过期、重复或与行业无关的记录删除。日期统一 ISO 8601，预算统一为人民币数值。"
					),
				},
				ensure_ascii=False,
			),
			[],
			run_id=run_id,
			purpose="analyze_lead_candidates",
		)
		if not isinstance(analyzed, list):
			raise RuntimeError("Qwen 未返回候选线索数组")
		seen: set[str] = set()
		candidates = []
		for item in analyzed:
			if not isinstance(item, dict):
				continue
			url = str(item.get("source_url") or "").strip()
			if not url:
				continue
			identity = "|".join(
				str(item.get(key) or "").strip().lower()
				for key in ("source_url", "project_number", "title", "purchaser")
			)
			fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
			if fingerprint in seen:
				continue
			seen.add(fingerprint)
			item["fingerprint"] = fingerprint
			candidates.append(item)
		self.store.stage(run_id, "analyzing", 70, f"已形成 {len(candidates)} 条可追溯候选线索", qwen="已完成")
		return {"criteria": criteria, "candidates": candidates}

	def review(
		self,
		state: LeadAgentState,
		arguments: ReviewCandidatesArguments,
	) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		candidates = list(state.get("candidates") or [])
		threshold = float(state["criteria"].get("score_threshold") or 70)
		qualified = [item for item in candidates if float(item.get("relevance_score") or 0) >= threshold]
		if not qualified:
			return {"candidates": candidates}
		self.store.stage(run_id, "reviewing", 78, "DeepSeek 正在复核高价值线索并制定跟进方案", deepseek="正在复核")
		prompt = (
			"你是企业招投标顾问。依据下面的已核验公开线索，为每条线索给出可执行的售前跟进方案。"
			"不要杜撰关系、资质或未公开信息。只返回 JSON 数组，每项包含 fingerprint 和 deepseek_plan。\n"
			+ json.dumps(qualified[: arguments.maximum_candidates], ensure_ascii=False)
		)
		partial = bool(state.get("partial"))
		try:
			plans = self.deepseek.review(prompt, run_id=run_id)
		except Exception as exc:
			partial = True
			self.store.stage(
				run_id,
				"reviewing",
				82,
				f"DeepSeek 暂不可用，正在降级 Qwen：{type(exc).__name__}",
				deepseek="已熔断或失败",
				qwen="正在接管复核",
			)
			plans = self.qwen.json(
				"你是企业招投标顾问。只依据输入证据生成跟进方案，只输出 JSON 数组。",
				prompt,
				[],
				run_id=run_id,
				purpose="lead_review_fallback",
			)
			if not isinstance(plans, list):
				plans = []
		plan_map = {
			item.get("fingerprint"): DeepSeekClient._plan_text(item.get("deepseek_plan"))
			for item in plans
			if isinstance(item, dict) and item.get("fingerprint")
		}
		fallback_plan = next(
			(
				DeepSeekClient._plan_text(item.get("deepseek_plan"))
				for item in plans
				if isinstance(item, dict) and item.get("deepseek_plan") and not item.get("fingerprint")
			),
			None,
		)
		matched = 0
		for candidate in qualified:
			candidate["deepseek_plan"] = plan_map.get(candidate.get("fingerprint")) or fallback_plan or ""
			if candidate["deepseek_plan"]:
				matched += 1
		if matched < len(qualified):
			partial = True
		self.store.stage(
			run_id,
			"reviewing",
			88,
			f"方案复核完成，{matched}/{len(qualified)} 条已生成方案",
			deepseek="已完成" if matched == len(qualified) else "部分完成",
		)
		return {"candidates": candidates, "partial": partial}

	def finish(
		self,
		state: LeadAgentState,
		_arguments: CompleteDiscoveryArguments,
	) -> dict[str, Any]:
		self.ensure_running(state["run_id"])
		summary = self._summary(state)
		self.store.stage(state["run_id"], "syncing", 95, "正在将结构化结果交给 Frappe 入库")
		return {"summary": summary, "final_answer": summary}

	@staticmethod
	def _summary(state: LeadAgentState) -> str:
		candidates = state.get("candidates") or []
		threshold = float((state.get("criteria") or {}).get("score_threshold") or 70)
		qualified = sum(float(item.get("relevance_score") or 0) >= threshold for item in candidates)
		summary = f"本次发现 {len(candidates)} 条可追溯候选线索，其中 {qualified} 条达到入库分数线。"
		if state.get("partial"):
			summary += " 部分辅助服务不可用，相关记录已标记，建议人工复核。"
		return summary

	def finalize(self, state: LeadAgentState) -> dict[str, Any]:
		answer = state.get("final_answer") or state.get("summary") or self._summary(state)
		messages = list(state.get("messages") or [])
		messages.append({"role": "assistant", "content": answer})
		return {
			"messages": messages[-60:],
			"final_answer": answer,
			"summary": state.get("summary") or answer,
			"status": "partial" if state.get("partial") else "completed",
		}

	def run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
		initial: LeadAgentState = {
			"run_id": run_id,
			"request": payload["request"],
			"user_id": payload.get("user_id") or "unknown",
			"tenant": payload.get("tenant") or "manager.myyr.top",
			"roles": payload.get("roles") or [],
			"profile": payload.get("profile") or {},
			"sources": payload.get("sources") or [],
		}
		config = {
			"configurable": {"thread_id": run_id},
			"recursion_limit": self.settings.max_agent_iterations * 3 + 10,
		}
		checkpoint = self.graph.get_state(config)
		if checkpoint.values:
			if checkpoint.next:
				return self.graph.invoke(None, config=config)
			return checkpoint.values
		return self.graph.invoke(initial, config=config)
