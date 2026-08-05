from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

from app.clients import DeepSeekClient, HermesClient, QwenClient, SearxngClient
from app.settings import Settings
from app.store import RunStore


class LeadState(TypedDict, total=False):
	run_id: str
	request: str
	profile: dict[str, Any]
	sources: list[dict[str, Any]]
	criteria: dict[str, Any]
	raw_candidates: list[dict[str, Any]]
	candidates: list[dict[str, Any]]
	summary: str
	partial: bool


class Stopped(RuntimeError):
	pass


class LeadWorkflow:
	def __init__(self, settings: Settings, store: RunStore) -> None:
		self.settings = settings
		self.store = store
		self.qwen = QwenClient(settings)
		self.hermes = HermesClient(settings)
		self.searxng = SearxngClient(settings)
		self.deepseek = DeepSeekClient(settings)
		builder = StateGraph(LeadState)
		builder.add_node("parse", self.parse_criteria)
		builder.add_node("research", self.research)
		builder.add_node("analyze", self.analyze)
		builder.add_node("review", self.review)
		builder.add_node("finish", self.finish)
		builder.add_edge(START, "parse")
		builder.add_edge("parse", "research")
		builder.add_edge("research", "analyze")
		builder.add_edge("analyze", "review")
		builder.add_edge("review", "finish")
		builder.add_edge("finish", END)
		checkpoint_connection = sqlite3.connect(
			settings.data_dir / "checkpoints.sqlite3",
			check_same_thread=False,
		)
		checkpoint_connection.execute("PRAGMA journal_mode=WAL")
		self.checkpointer = SqliteSaver(checkpoint_connection)
		self.graph = builder.compile(checkpointer=self.checkpointer)

	def ensure_running(self, run_id: str) -> None:
		run = self.store.get(run_id)
		if not run or run["stop_requested"]:
			raise Stopped("任务已停止")

	def parse_criteria(self, state: LeadState) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		self.store.stage(run_id, "parsing", 10, "Qwen 正在理解获客目标", qwen="正在解析")
		profile = state.get("profile") or {}
		criteria = self.qwen.json(
			"你是企业获客任务解析器。只输出一个 JSON 对象，不要解释。未知信息使用 null，不得编造。",
			json.dumps(
				{
					"today": date.today().isoformat(),
					"request": state["request"],
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
		)
		criteria = {**profile, **(criteria if isinstance(criteria, dict) else {})}
		criteria["maximum_results"] = max(1, min(100, int(criteria.get("maximum_results") or 30)))
		criteria["score_threshold"] = max(0, min(100, float(criteria.get("score_threshold") or 70)))
		return {"criteria": criteria}

	def research(self, state: LeadState) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		criteria = state["criteria"]
		self.store.stage(run_id, "researching", 30, "Hermes 正在检索并核验近期公开招标信息", hermes="正在调研", qwen="已完成")
		sources = state.get("sources") or []
		prompt = (
			"你是 I-ONE 行业情报研究员。联网检索近期真实、仍有业务价值的招标/采购公告。"
			"优先官方来源，逐条核对原文，不得编造项目、预算、联系人或网址。"
			"只返回 JSON 数组。每项字段：title,project_number,purchaser,agency,contact_name,"
			"contact_phone,contact_email,source_name,source_url,published_at,deadline,budget,region,"
			"industry,procurement_method,raw_text,evidence。evidence 是包含 url、snippet 的数组。\n"
			f"任务：{state['request']}\n条件：{json.dumps(criteria, ensure_ascii=False)}\n"
			f"优先来源：{json.dumps(sources, ensure_ascii=False)}"
		)
		partial = False
		try:
			raw = self.hermes.research(prompt)
		except Exception:
			partial = True
			industry = criteria.get("industry") or state["request"]
			regions = criteria.get("regions") or [""]
			keywords = criteria.get("keywords") or ["招标", "采购"]
			queries = [f"{region} {industry} {keyword} 招标 采购".strip() for region in regions for keyword in keywords]
			raw = self.searxng.search(queries, limit=criteria["maximum_results"])
		self.store.stage(
			run_id,
			"researching",
			45,
			f"已获取 {len(raw)} 条公开信息，准备结构化分析",
			hermes="已完成" if not partial else "降级为搜索引擎",
		)
		return {"raw_candidates": raw[: criteria["maximum_results"]], "partial": partial}

	def analyze(self, state: LeadState) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		self.store.stage(run_id, "analyzing", 55, "Qwen 正在提取需求、评分并去重", qwen="正在分析")
		raw = state.get("raw_candidates") or []
		if not raw:
			return {"candidates": []}
		compact = []
		for item in raw:
			compact.append({key: value for key, value in item.items() if key != "raw_text"} | {"raw_text": str(item.get("raw_text") or "")[:4000]})
		analyzed = self.qwen.json(
			"你是严谨的招标线索分析师。只使用输入证据，禁止补造。只输出 JSON 数组。",
			json.dumps(
				{
					"criteria": state["criteria"],
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
		)
		if not isinstance(analyzed, list):
			analyzed = []
		seen: set[str] = set()
		candidates = []
		for item in analyzed:
			url = str(item.get("source_url") or "").strip()
			if not url:
				continue
			identity = "|".join(str(item.get(key) or "").strip().lower() for key in ("source_url", "project_number", "title", "purchaser"))
			fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
			if fingerprint in seen:
				continue
			seen.add(fingerprint)
			item["fingerprint"] = fingerprint
			candidates.append(item)
		self.store.stage(run_id, "analyzing", 70, f"已形成 {len(candidates)} 条可追溯候选线索", qwen="已完成")
		return {"candidates": candidates}

	def review(self, state: LeadState) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		candidates = state.get("candidates") or []
		threshold = float(state["criteria"].get("score_threshold") or 70)
		qualified = [item for item in candidates if float(item.get("relevance_score") or 0) >= threshold]
		if not qualified:
			return {}
		self.store.stage(run_id, "reviewing", 78, "DeepSeek 正在复核高价值线索并制定跟进方案", deepseek="正在复核")
		prompt = (
			"你是企业招投标顾问。依据下面的已核验公开线索，为每条线索给出可执行的售前跟进方案。"
			"不要杜撰关系、资质或未公开信息。只返回 JSON 数组，每项包含 fingerprint 和 deepseek_plan。\n"
			+ json.dumps(qualified[:20], ensure_ascii=False)
		)
		partial = bool(state.get("partial"))
		try:
			plans = self.deepseek.review(prompt)
			plan_map = {item.get("fingerprint"): item.get("deepseek_plan") for item in plans if isinstance(item, dict)}
			for candidate in candidates:
				if candidate.get("fingerprint") in plan_map:
					candidate["deepseek_plan"] = plan_map[candidate["fingerprint"]]
			self.store.stage(run_id, "reviewing", 88, "DeepSeek 方案复核完成", deepseek="已完成")
		except Exception as exc:
			partial = True
			self.store.stage(run_id, "reviewing", 88, f"DeepSeek 暂不可用，已保留 Qwen 分析：{type(exc).__name__}", deepseek="暂不可用")
		return {"candidates": candidates, "partial": partial}

	def finish(self, state: LeadState) -> dict[str, Any]:
		run_id = state["run_id"]
		self.ensure_running(run_id)
		candidates = state.get("candidates") or []
		threshold = float(state["criteria"].get("score_threshold") or 70)
		qualified = sum(float(item.get("relevance_score") or 0) >= threshold for item in candidates)
		summary = f"本次发现 {len(candidates)} 条可追溯候选线索，其中 {qualified} 条达到入库分数线。"
		if state.get("partial"):
			summary += " 部分辅助服务不可用，相关记录已标记，建议人工复核。"
		self.store.stage(run_id, "syncing", 95, "正在将结构化结果交给 Frappe 入库")
		return {"summary": summary}

	def run(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
		initial: LeadState = {
			"run_id": run_id,
			"request": payload["request"],
			"profile": payload.get("profile") or {},
			"sources": payload.get("sources") or [],
		}
		config = {"configurable": {"thread_id": run_id}, "recursion_limit": 20}
		checkpoint = self.graph.get_state(config)
		if checkpoint.values:
			if checkpoint.next:
				return self.graph.invoke(None, config=config)
			return checkpoint.values
		return self.graph.invoke(initial, config=config)
