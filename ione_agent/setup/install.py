from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = ("I-ONE Agent User", "I-ONE Agent Manager")


def _ensure_roles() -> None:
	for role_name in ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)


def _ensure_crm_fields() -> None:
	if not frappe.db.exists("DocType", "CRM Lead"):
		return
	create_custom_fields(
		{
			"CRM Lead": [
				{"fieldname": "custom_ione_ai_section", "label": "I-ONE AI 获客", "fieldtype": "Section Break", "insert_after": "source"},
				{"fieldname": "custom_ione_candidate", "label": "AI 候选线索", "fieldtype": "Link", "options": "I-ONE Lead Candidate", "insert_after": "custom_ione_ai_section", "read_only": 1},
				{"fieldname": "custom_ione_project_number", "label": "招标项目编号", "fieldtype": "Data", "insert_after": "custom_ione_candidate", "read_only": 1},
				{"fieldname": "custom_ione_source_url", "label": "招标原文", "fieldtype": "Data", "insert_after": "custom_ione_project_number", "read_only": 1},
				{"fieldname": "custom_ione_budget", "label": "项目预算", "fieldtype": "Currency", "insert_after": "custom_ione_source_url", "read_only": 1},
				{"fieldname": "custom_ione_deadline", "label": "投标截止时间", "fieldtype": "Datetime", "insert_after": "custom_ione_budget", "read_only": 1},
				{"fieldname": "custom_ione_relevance_score", "label": "AI 相关度", "fieldtype": "Percent", "insert_after": "custom_ione_deadline", "read_only": 1},
				{"fieldname": "custom_ione_risk_level", "label": "AI 风险等级", "fieldtype": "Select", "options": "低\n中\n高", "insert_after": "custom_ione_relevance_score", "read_only": 1},
				{"fieldname": "custom_ione_ai_status", "label": "AI 核验状态", "fieldtype": "Select", "options": "待人工核验\n已核验\n已归档", "default": "待人工核验", "insert_after": "custom_ione_risk_level"},
			]
		},
		update=True,
	)


def _ensure_default_sources() -> None:
	if not frappe.db.exists("DocType", "I-ONE Lead Source"):
		return
	for source in (
		{"source_name": "中国政府采购网", "source_type": "政府采购", "base_url": "https://www.ccgp.gov.cn", "priority": 100},
		{"source_name": "全国公共资源交易平台", "source_type": "公共资源交易", "base_url": "https://www.ggzy.gov.cn", "priority": 90},
	):
		if frappe.db.exists("I-ONE Lead Source", source["source_name"]):
			continue
		frappe.get_doc(
			{
				"doctype": "I-ONE Lead Source",
				"user": "Administrator",
				"enabled": 1,
				"official_source": 1,
				**source,
			}
		).insert(ignore_permissions=True)


def _ensure_business_setup() -> None:
	_ensure_crm_fields()
	_ensure_default_sources()


def before_install() -> None:
	_ensure_roles()


def after_install() -> None:
	_ensure_roles()
	_ensure_business_setup()
	frappe.clear_cache()


def after_migrate() -> None:
	_ensure_roles()
	_ensure_business_setup()
	frappe.clear_cache()
