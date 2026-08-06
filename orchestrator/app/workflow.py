from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, ClassVar
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


class PlanCompletionCriteria(BaseModel):
	minimum_verified_sources: int = Field(default=10, ge=1, le=100)
	minimum_qualified_leads: int = Field(default=3, ge=0, le=100)
	maximum_search_rounds: int = Field(default=1, ge=1, le=3)


class InitialExecutionPlan(BaseModel):
	intent: str = Field(default="lead_discovery", min_length=1, max_length=80)
	goal: str = Field(min_length=1, max_length=1000)
	search_strategy: dict[str, Any] = Field(default_factory=dict)
	required_tools: list[str] = Field(default_factory=list, max_length=20)
	completion_criteria: PlanCompletionCriteria = Field(default_factory=PlanCompletionCriteria)
	requires_final_review: bool = True
	rationale: str = Field(default="", max_length=2000)


class Stopped(RuntimeError):
	pass


class LeadWorkflow:
	DEFAULT_TOOL_PLAN: ClassVar[tuple[str, ...]] = (
		"parse_lead_request",
		"search_public_tenders",
		"analyze_lead_candidates",
		"review_qualified_leads",
		"complete_lead_discovery",
	)
	TOOL_DEPENDENCIES: ClassVar[dict[str, frozenset[str]]] = {
		"parse_lead_request": frozenset(),
		"search_public_tenders": frozenset({"parse_lead_request"}),
		"analyze_lead_candidates": frozenset({"search_public_tenders"}),
		"review_qualified_leads": frozenset({"analyze_lead_candidates"}),
		"complete_lead_discovery": frozenset({"analyze_lead_candidates"}),
	}

	def __init__(self, settings: Settings, store: RunStore) -> None:
		self.settings = settings
		self.store = store
		self.qwen = QwenClient(settings, audit=store.record_model_call)
		self.hermes = HermesClient(settings)
		self.searxng = SearxngClient(settings)
		self.extractor = WebPageExtractor(settings)
		self.deepseek = DeepSeekClient(settings, audit=store.record_model_call)
		self.router = ModelRouter(settings, self.qwen)
		self.registry = self._build_registry()
		self.tool_node = GovernedToolNode(self.registry, ToolPolicy(), store)

		builder = StateGraph(LeadAgentState)
		builder.add_node("initialize", self.initialize)
		builder.add_node("planner", self.create_initial_plan)
		builder.add_node("model", self.model)
		builder.add_node("tools", self.tool_node)
		builder.add_node("finalize", self.finalize)
		builder.add_edge(START, "initialize")
		builder.add_edge("initialize", "planner")
		builder.add_edge("planner", "model")
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
				max_attempts=1,
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
			"plan": [],
			"planning_complete": False,
			"planning_model": "",
			"planning_error": "",
			"goal": state["request"][:1000],
			"search_strategy": {},
			"completion_criteria": {},
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

	def _fallback_initial_plan(self, state: LeadAgentState) -> dict[str, Any]:
		profile = dict(state.get("profile") or {})
		maximum_results = max(1, min(100, int(profile.get("maximum_results") or 30)))
		return {
			"intent": "lead_discovery",
			"goal": str(state.get("request") or "发现并分析公开业务线索")[:1000],
			"search_strategy": {
				"industry": profile.get("industry"),
				"regions": profile.get("regions") or [],
				"keywords": profile.get("keywords") or [],
				"days_back": profile.get("days_back") or 30,
				"maximum_results": maximum_results,
			},
			"required_tools": list(self.DEFAULT_TOOL_PLAN),
			"completion_criteria": {
				"minimum_verified_sources": min(10, maximum_results),
				"minimum_qualified_leads": min(3, maximum_results),
				"maximum_search_rounds": 1,
			},
			"requires_final_review": True,
			"rationale": "使用安全的默认获客计划继续执行。",
		}

	def _normalize_initial_plan(
		self,
		payload: Any,
		state: LeadAgentState,
	) -> InitialExecutionPlan:
		if isinstance(payload, dict) and isinstance(payload.get("plan"), dict):
			payload = payload["plan"]
		plan = InitialExecutionPlan.model_validate(payload)
		allowed = set(self.registry.names())
		requested = [name for name in plan.required_tools if name in allowed]
		for required in (
			"parse_lead_request",
			"search_public_tenders",
			"analyze_lead_candidates",
			"complete_lead_discovery",
		):
			if required not in requested:
				requested.append(required)
		if plan.requires_final_review and "review_qualified_leads" not in requested:
			requested.append("review_qualified_leads")
		requested_set = set(requested)
		for name in tuple(requested):
			requested_set.update(self.TOOL_DEPENDENCIES.get(name, ()))
		ordered = [name for name in self.DEFAULT_TOOL_PLAN if name in requested_set]
		if not ordered:
			raise ValueError("执行计划没有可用工具")
		plan.required_tools = ordered
		plan.search_strategy = {
			**dict(state.get("profile") or {}),
			**dict(plan.search_strategy or {}),
		}
		return plan

	def create_initial_plan(self, state: LeadAgentState) -> dict[str, Any]:
		self.ensure_running(state["run_id"])
		if state.get("planning_complete"):
			return {}
		run_id = state["run_id"]
		fallback = self._fallback_initial_plan(state)
		planning_payload = {
			"today": date.today().isoformat(),
			"request": state.get("request"),
			"saved_profile": state.get("profile") or {},
			"trusted_sources": state.get("sources") or [],
			"available_tools": self.registry.definitions(),
			"required_schema": {
				"intent": "lead_discovery",
				"goal": "string",
				"search_strategy": {
					"industry": "string|null",
					"regions": ["string"],
					"keywords": ["string"],
					"days_back": "integer",
					"maximum_results": "integer",
				},
				"required_tools": ["registered tool name"],
				"completion_criteria": {
					"minimum_verified_sources": "integer",
					"minimum_qualified_leads": "integer",
					"maximum_search_rounds": "integer",
				},
				"requires_final_review": "boolean",
				"rationale": "string",
			},
		}
		prompt = (
			"你是 I-ONE Agent 的首席任务规划模型。请先理解用户目标，再生成生产级执行计划。"
			"只输出一个符合 required_schema 的 JSON 对象，不要 Markdown。"
			"只能使用 available_tools 中的工具，不得生成代码、Shell、SQL 或虚构数据。"
			"计划必须包含需求解析、公开信息检索、证据分析和完成步骤；需要售前方案时启用最终复核。\n"
			+ json.dumps(planning_payload, ensure_ascii=False)
		)
		self.store.stage(run_id, "planning", 7, "DeepSeek 正在理解目标并制定执行计划", deepseek="正在规划")
		planning_model = "deepseek"
		planning_errors: list[str] = []
		try:
			raw_plan = self.deepseek.json(
				prompt,
				{},
				timeout=self.settings.deepseek_planning_timeout_seconds,
				run_id=run_id,
				purpose="lead_initial_planning",
			)
			plan = self._normalize_initial_plan(raw_plan, state)
		except Exception as exc:
			planning_errors.append(f"DeepSeek {type(exc).__name__}: {exc}")
			planning_model = "qwen"
			self.store.stage(
				run_id,
				"planning",
				8,
				f"DeepSeek 规划暂不可用，正在降级 Qwen：{type(exc).__name__}",
				deepseek="规划失败，已降级",
				qwen="正在接管规划",
			)
			try:
				raw_plan = self.qwen.json(
					"你是企业任务规划模型。只根据输入生成严格 JSON 执行计划，不执行工具、不编造信息。",
					json.dumps(planning_payload, ensure_ascii=False),
					{},
					timeout=90,
					max_attempts=1,
					run_id=run_id,
					purpose="lead_initial_planning_fallback",
				)
				plan = self._normalize_initial_plan(raw_plan, state)
			except Exception as fallback_exc:
				planning_errors.append(f"Qwen {type(fallback_exc).__name__}: {fallback_exc}")
				planning_model = "deterministic"
				plan = self._normalize_initial_plan(fallback, state)
		self.store.stage(
			run_id,
			"planning",
			10,
			f"执行计划已生成，规划模型：{planning_model}",
			deepseek="规划完成" if planning_model == "deepseek" else "已降级",
			qwen="规划完成" if planning_model == "qwen" else "等待执行",
		)
		return {
			"planning_complete": True,
			"planning_model": planning_model,
			"planning_error": "\n".join(planning_errors)[:4000],
			"intent": {"name": plan.intent, "confidence": 1.0},
			"goal": plan.goal,
			"search_strategy": plan.search_strategy,
			"completion_criteria": plan.completion_criteria.model_dump(mode="json"),
			"plan": plan.required_tools,
		}

	def _eligible_tools(self, state: LeadAgentState) -> list[str]:
		completed = set(state.get("completed_tools") or [])
		plan = state.get("plan") or list(self.DEFAULT_TOOL_PLAN)
		eligible = []
		for name in plan:
			if name in completed or name not in self.TOOL_DEPENDENCIES:
				continue
			dependencies = self.TOOL_DEPENDENCIES[name]
			if name == "complete_lead_discovery" and "review_qualified_leads" in plan:
				dependencies = dependencies | {"review_qualified_leads"}
			if dependencies.issubset(completed):
				eligible.append(name)
		return eligible

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
		eligible = self._eligible_tools(state)
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
			tools=[item for item in self.registry.definitions() if item["name"] in eligible],
			eligible_tools=eligible,
		)
		decision = self._guard_decision(decision, eligible, state)
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
			"eligible_tools": eligible,
			"messages": messages[-60:],
			"iteration_count": iteration,
			"status": "running",
		}

	def _guard_decision(
		self,
		decision: AgentDecision,
		eligible_tools: list[str],
		state: LeadAgentState,
	) -> AgentDecision:
		if not eligible_tools:
			if decision.type == "answer":
				return decision
			return AgentDecision(
				type="answer",
				content=state.get("summary") or self._summary(state),
				reason="所有计划步骤已经完成。",
			)

		call = decision.tool_call
		if decision.type != "tool_call" or not call or call.name not in eligible_tools:
			fallback_tool = eligible_tools[0]
			call = AgentToolCall(
				id=f"call_{uuid4().hex[:16]}",
				name=fallback_tool,
				arguments=self._default_arguments(fallback_tool, state),
			)
			return AgentDecision(
				type="tool_call",
				tool_call=call,
				reason="策略守卫从当前满足依赖条件的计划工具中选择下一步。",
			)
		defaults = self._default_arguments(call.name, state)
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
		partial = bool(state.get("partial"))
		try:
			criteria = self.qwen.json(
				"你是企业获客任务解析器。只输出一个 JSON 对象，不要解释。未知信息使用 null，不得编造。",
				json.dumps(
					{
						"today": date.today().isoformat(),
						"request": arguments.request or state["request"],
						"saved_profile": profile,
						"deepseek_plan": state.get("search_strategy") or {},
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
				timeout=90,
				max_attempts=1,
				run_id=run_id,
				purpose="parse_lead_criteria",
			)
		except Exception as exc:
			partial = True
			criteria = {}
			self.store.stage(
				run_id,
				"parsing",
				16,
				f"Qwen 解析暂不可用，正在使用已验证的首次计划：{type(exc).__name__}",
				qwen="解析已降级",
			)
		criteria = {
			**profile,
			**dict(state.get("search_strategy") or {}),
			**(criteria if isinstance(criteria, dict) else {}),
		}
		criteria["maximum_results"] = max(1, min(100, int(criteria.get("maximum_results") or 30)))
		criteria["score_threshold"] = max(0, min(100, float(criteria.get("score_threshold") or 70)))
		return {"criteria": criteria, "partial": partial}

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
		partial = bool(state.get("partial"))
		try:
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
				timeout=180,
				max_attempts=1,
				run_id=run_id,
				purpose="analyze_lead_candidates",
			)
		except Exception as exc:
			partial = True
			self.store.stage(
				run_id,
				"analyzing",
				62,
				f"Qwen 暂不可用，正在依据已核验证据保守评分：{type(exc).__name__}",
				qwen="已降级",
			)
			analyzed = self._fallback_analysis(raw, criteria)
		if not isinstance(analyzed, list) or (not analyzed and raw):
			partial = True
			analyzed = self._fallback_analysis(raw, criteria)
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
		return {"criteria": criteria, "candidates": candidates, "partial": partial}

	@staticmethod
	def _fallback_analysis(raw: list[dict[str, Any]], criteria: dict[str, Any]) -> list[dict[str, Any]]:
		industry = str(criteria.get("industry") or "").strip().lower()
		keywords = [str(value).strip().lower() for value in criteria.get("keywords") or [] if value]
		fallback = []
		for source in raw:
			url = str(source.get("source_url") or "").strip()
			if not url:
				continue
			text = " ".join(
				str(source.get(key) or "") for key in ("title", "raw_text", "industry", "purchaser")
			).lower()
			official = any(marker in url.lower() for marker in (".gov.cn", "ccgp.gov.cn"))
			matched_keywords = sum(keyword in text for keyword in keywords)
			score = 50
			if industry and industry in text:
				score += 20
			score += min(20, matched_keywords * 8)
			if official:
				score += 10
			if source.get("evidence"):
				score += 5
			item = dict(source)
			item.update(
				{
					"relevance_score": min(100, score),
					"confidence": 70 if official and source.get("evidence") else 50,
					"risk_level": "中",
					"requirement_summary": str(source.get("raw_text") or source.get("title") or "")[:500],
					"qualification_requirements": source.get("qualification_requirements") or "待获取招标文件核验",
					"recommendation": "本地模型繁忙，已依据公开证据完成保守评分，建议人工复核后跟进。",
				}
			)
			fallback.append(item)
		return fallback

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
			try:
				plans = self.qwen.json(
					"你是企业招投标顾问。只依据输入证据生成跟进方案，只输出 JSON 数组。",
					prompt,
					[],
					timeout=90,
					max_attempts=1,
					run_id=run_id,
					purpose="lead_review_fallback",
				)
			except Exception as fallback_exc:
				plans = []
				self.store.stage(
					run_id,
					"reviewing",
					85,
					f"Qwen 复核超时，正在生成证据驱动的保守方案：{type(fallback_exc).__name__}",
					qwen="已降级",
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
			candidate["deepseek_plan"] = (
				plan_map.get(candidate.get("fingerprint"))
				or fallback_plan
				or self._fallback_review_plan(candidate)
			)
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

	@staticmethod
	def _fallback_review_plan(candidate: dict[str, Any]) -> str:
		title = str(candidate.get("title") or "该项目").strip()
		project_number = str(candidate.get("project_number") or "").strip()
		deadline = str(candidate.get("deadline") or "").strip()
		purchaser = str(candidate.get("purchaser") or "").strip()
		agency = str(candidate.get("agency") or "").strip()
		requirements = str(
			candidate.get("qualification_requirements")
			or candidate.get("requirement_summary")
			or ""
		).strip()
		source_url = str(candidate.get("source_url") or "").strip()
		steps = [f"核验《{title}》公告原文及后续更正公告。"]
		if project_number:
			steps.append(f"以项目编号 {project_number} 建立内部机会档案并检查重复记录。")
		if deadline:
			steps.append(f"围绕截止时间 {deadline} 倒排报名、答疑、方案、报价和投标文件准备计划。")
		else:
			steps.append("从公告或招标文件核实报名、答疑、投标截止和开标时间。")
		contacts = "、".join(value for value in (purchaser, agency) if value)
		if contacts:
			steps.append(f"通过公告公开渠道联系 {contacts}，确认采购范围、文件获取方式和澄清安排。")
		if requirements:
			steps.append(f"逐项核对资格与需求：{requirements[:240]}")
		steps.append("组织销售、售前和交付人员完成匹配度、资质、案例、成本及风险复核后再决定是否跟进。")
		if source_url:
			steps.append(f"全程以官方来源为准：{source_url}")
		return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))

	def finish(
		self,
		state: LeadAgentState,
		_arguments: CompleteDiscoveryArguments,
	) -> dict[str, Any]:
		self.ensure_running(state["run_id"])
		evaluation = self._evaluate_completion(state)
		partial = bool(state.get("partial")) or not evaluation["met"]
		summary = self._summary({**state, "partial": partial})
		if evaluation["shortfalls"]:
			summary += " 未满足的完成条件：" + "；".join(evaluation["shortfalls"]) + "。"
		self.store.stage(state["run_id"], "syncing", 95, "正在将结构化结果交给 Frappe 入库")
		return {
			"summary": summary,
			"final_answer": summary,
			"completion_evaluation": evaluation,
			"partial": partial,
		}

	@staticmethod
	def _evaluate_completion(state: LeadAgentState) -> dict[str, Any]:
		candidates = state.get("candidates") or []
		criteria = state.get("completion_criteria") or {}
		score_threshold = float((state.get("criteria") or {}).get("score_threshold") or 70)
		verified_sources = sum(
			bool(item.get("source_url") and item.get("evidence")) for item in candidates
		)
		qualified_leads = sum(
			float(item.get("relevance_score") or 0) >= score_threshold for item in candidates
		)
		minimum_sources = int(criteria.get("minimum_verified_sources") or 1)
		minimum_leads = int(criteria.get("minimum_qualified_leads") or 0)
		shortfalls = []
		if verified_sources < minimum_sources:
			shortfalls.append(f"可核验来源 {verified_sources}/{minimum_sources}")
		if qualified_leads < minimum_leads:
			shortfalls.append(f"合格线索 {qualified_leads}/{minimum_leads}")
		return {
			"met": not shortfalls,
			"verified_sources": verified_sources,
			"qualified_leads": qualified_leads,
			"shortfalls": shortfalls,
		}

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
