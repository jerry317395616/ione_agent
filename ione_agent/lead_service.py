from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint, flt, now_datetime
from frappe.utils.file_manager import save_file

from ione_agent.temporal import normalize_datetime

PROFILE_DTYPE = "I-ONE Lead Discovery Profile"
SOURCE_DTYPE = "I-ONE Lead Source"
TASK_DTYPE = "I-ONE Lead Discovery Task"
CANDIDATE_DTYPE = "I-ONE Lead Candidate"

TASK_STATUS_MAP = {
	"queued": "等待执行",
	"parsing": "正在解析",
	"researching": "正在调研",
	"analyzing": "正在分析",
	"reviewing": "正在复核",
	"syncing": "正在入库",
	"completed": "已完成",
	"partial": "部分完成",
	"failed": "失败",
	"stopped": "已停止",
}


def build_discovery_payload(task, profile=None) -> dict[str, Any]:
	profile = profile or (frappe.get_doc(PROFILE_DTYPE, task.profile) if task.profile else None)
	sources = frappe.get_all(
		SOURCE_DTYPE,
		filters={"enabled": 1},
		fields=["source_name", "source_type", "official_source", "priority", "base_url", "search_hint"],
		order_by="priority desc",
	)
	configuration = {}
	if profile:
		configuration = {
			"profile_name": profile.profile_name,
			"industry": profile.industry,
			"regions": profile.regions,
			"keywords": profile.keywords,
			"excluded_keywords": profile.excluded_keywords,
			"days_back": cint(profile.days_back),
			"minimum_budget": flt(profile.minimum_budget),
			"maximum_results": cint(profile.maximum_results),
			"score_threshold": flt(profile.score_threshold),
			"source_notes": profile.source_notes,
		}
	return {
		"client_run_id": task.agent_run,
		"task_id": task.name,
		"user_id": task.user,
		"request": task.original_request,
		"profile": configuration,
		"sources": [dict(row) for row in sources],
	}


def create_task(*, user: str, request: str, agent_run: str, profile: str | None = None):
	if profile:
		profile_doc = frappe.get_doc(PROFILE_DTYPE, profile)
		if profile_doc.user != user and user != "Administrator":
			frappe.throw("你无权使用该获客配置。", frappe.PermissionError)
	else:
		profile_doc = None
	return frappe.get_doc(
		{
			"doctype": TASK_DTYPE,
			"profile": profile_doc.name if profile_doc else None,
			"user": user,
			"agent_run": agent_run,
			"original_request": request,
			"status": "等待执行",
			"current_stage": "等待编排服务接收任务",
		}
	).insert(ignore_permissions=True)


def _candidate_values(task, item: dict[str, Any]) -> dict[str, Any]:
	return {
		"doctype": CANDIDATE_DTYPE,
		"user": task.user,
		"task": task.name,
		"title": item.get("title") or "未命名采购项目",
		"status": item.get("status") or "已分析",
		"relevance_score": flt(item.get("relevance_score")),
		"confidence": flt(item.get("confidence")),
		"risk_level": item.get("risk_level") or "中",
		"project_number": item.get("project_number"),
		"industry": item.get("industry"),
		"region": item.get("region"),
		"procurement_method": item.get("procurement_method"),
		"purchaser": item.get("purchaser"),
		"agency": item.get("agency"),
		"published_at": normalize_datetime(item.get("published_at")),
		"deadline": normalize_datetime(item.get("deadline")),
		"budget": flt(item.get("budget")),
		"contact_name": item.get("contact_name"),
		"contact_phone": item.get("contact_phone"),
		"contact_email": item.get("contact_email"),
		"source_name": item.get("source_name"),
		"source_url": item.get("source_url"),
		"fingerprint": item.get("fingerprint"),
		"evidence_json": json.dumps(item.get("evidence") or [], ensure_ascii=False, indent=2),
		"requirement_summary": item.get("requirement_summary"),
		"qualification_requirements": item.get("qualification_requirements"),
		"recommendation": item.get("recommendation"),
		"deepseek_plan": item.get("deepseek_plan"),
		"raw_text": item.get("raw_text"),
	}


def _analysis_markdown(candidate) -> str:
	return "\n".join(
		[
			f"# {candidate.title}",
			"",
			f"- 项目编号：{candidate.project_number or '未公布'}",
			f"- 采购人：{candidate.purchaser or '未识别'}",
			f"- 预算：{candidate.budget or '未公布'}",
			f"- 截止时间：{candidate.deadline or '未公布'}",
			f"- 原文：{candidate.source_url}",
			f"- 相关度：{candidate.relevance_score}%",
			f"- 可信度：{candidate.confidence}%",
			"",
			"## 需求摘要",
			candidate.requirement_summary or "暂无",
			"",
			"## 资质要求",
			candidate.qualification_requirements or "暂无",
			"",
			"## 跟进建议",
			candidate.recommendation or "暂无",
			"",
			"## DeepSeek 方案",
			candidate.deepseek_plan or "暂无",
		]
	)


def attach_analysis(candidate) -> str:
	if candidate.analysis_attachment:
		return candidate.analysis_attachment
	file_doc = save_file(
		f"{candidate.name}-AI分析方案.md",
		_analysis_markdown(candidate).encode("utf-8"),
		CANDIDATE_DTYPE,
		candidate.name,
		is_private=1,
	)
	candidate.db_set("analysis_attachment", file_doc.file_url, update_modified=False)
	return file_doc.file_url


@frappe.whitelist()
def create_crm_lead(candidate_name: str, *, force: bool = False) -> str | None:
	candidate = frappe.get_doc(CANDIDATE_DTYPE, candidate_name)
	roles = set(frappe.get_roles())
	if (
		frappe.session.user not in {"Administrator", candidate.user}
		and not {"System Manager", "I-ONE Agent Manager"}.intersection(roles)
	):
		frappe.throw("你无权处理该候选线索。", frappe.PermissionError)
	if candidate.crm_lead:
		return candidate.crm_lead
	if not frappe.db.exists("DocType", "CRM Lead"):
		if force:
			frappe.throw("当前站点未安装 Frappe CRM。")
		return None

	meta = frappe.get_meta("CRM Lead")
	lead = frappe.new_doc("CRM Lead")
	lead.first_name = candidate.contact_name or candidate.purchaser or candidate.title
	for fieldname, value in {
		"organization": candidate.purchaser,
		"email": candidate.contact_email,
		"mobile_no": candidate.contact_phone,
		"company_description": candidate.requirement_summary,
		"territory": candidate.region,
		"custom_ione_candidate": candidate.name,
		"custom_ione_project_number": candidate.project_number,
		"custom_ione_source_url": candidate.source_url,
		"custom_ione_budget": candidate.budget,
		"custom_ione_deadline": candidate.deadline,
		"custom_ione_relevance_score": candidate.relevance_score,
		"custom_ione_risk_level": candidate.risk_level,
		"custom_ione_ai_status": "待人工核验",
	}.items():
		if value not in (None, "") and meta.has_field(fieldname):
			lead.set(fieldname, value)
	lead.insert(ignore_permissions=True)
	attachment = attach_analysis(candidate)
	if attachment:
		file_name = frappe.db.get_value("File", {"file_url": attachment}, "name")
		if file_name:
			frappe.db.set_value("File", file_name, {"attached_to_doctype": "CRM Lead", "attached_to_name": lead.name})
	candidate.db_set(
		{"crm_lead": lead.name, "crm_created_at": now_datetime(), "status": "已创建 CRM 线索"},
		update_modified=False,
	)
	return lead.name


def ingest_result(task, result: dict[str, Any]) -> dict[str, int]:
	counts = {"found": 0, "qualified": 0, "crm_created": 0, "review": 0}
	profile = frappe.get_doc(PROFILE_DTYPE, task.profile) if task.profile else None
	threshold = flt(profile.score_threshold if profile else 70)
	for item in result.get("candidates") or []:
		fingerprint = item.get("fingerprint")
		if fingerprint and frappe.db.exists(CANDIDATE_DTYPE, {"fingerprint": fingerprint}):
			continue
		candidate = frappe.get_doc(_candidate_values(task, item)).insert(ignore_permissions=True)
		counts["found"] += 1
		if candidate.relevance_score >= threshold:
			candidate.db_set("status", "合格", update_modified=False)
			counts["qualified"] += 1
			if profile and profile.auto_create_crm:
				if create_crm_lead(candidate.name):
					counts["crm_created"] += 1
		else:
			candidate.db_set("status", "待人工核验", update_modified=False)
			counts["review"] += 1
	return counts


def sync_task(task_name: str, payload: dict[str, Any]) -> None:
	task = frappe.get_doc(TASK_DTYPE, task_name)
	stage = str(payload.get("stage") or payload.get("status") or "").lower()
	task.status = TASK_STATUS_MAP.get(stage, task.status)
	task.progress = flt(payload.get("progress"))
	task.current_stage = payload.get("current_stage") or task.current_stage
	task.error_message = payload.get("error") or ""
	components = payload.get("components") or {}
	task.qwen_status = components.get("qwen") or task.qwen_status
	task.hermes_status = components.get("hermes") or task.hermes_status
	task.deepseek_status = components.get("deepseek") or task.deepseek_status
	if payload.get("started_at"):
		task.started_at = normalize_datetime(payload["started_at"])
	if payload.get("completed_at"):
		task.completed_at = normalize_datetime(payload["completed_at"])
	result = payload.get("result") or {}
	if result.get("criteria"):
		task.criteria_json = json.dumps(result["criteria"], ensure_ascii=False, indent=2)
	if task.status in {"已完成", "部分完成"} and not cint(task.found_count):
		counts = ingest_result(task, result)
		task.found_count = counts["found"]
		task.qualified_count = counts["qualified"]
		task.crm_created_count = counts["crm_created"]
		task.review_count = counts["review"]
	task.result_summary = result.get("summary") or task.result_summary
	task.save(ignore_permissions=True)
