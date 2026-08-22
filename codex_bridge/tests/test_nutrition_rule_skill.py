from pathlib import Path

from app.bridge import CodexBridge
from app.settings import DEFAULT_INSTRUCTIONS, DEFAULT_MCP_TOOLS

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "manage-tongjianyun-nutrition-rules"


def test_nutrition_rule_tools_are_enabled_by_default() -> None:
	expected = {
		"frappe_list_tongjianyun_nutrition_rules",
		"frappe_create_tongjianyun_nutrition_rule_draft",
		"frappe_preview_tongjianyun_nutrition_rule",
		"frappe_submit_tongjianyun_nutrition_rule",
		"frappe_publish_tongjianyun_nutrition_rule",
		"frappe_rollback_tongjianyun_nutrition_rule",
	}
	assert expected.issubset(DEFAULT_MCP_TOOLS)
	assert "create a versioned draft" in DEFAULT_INSTRUCTIONS
	assert "Never bypass role checks" in DEFAULT_INSTRUCTIONS


def test_nutrition_rule_skill_enforces_preview_and_confirmation() -> None:
	content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
	contract = (SKILL_ROOT / "references" / "rule-contract.md").read_text(encoding="utf-8")
	assert "frappe_preview_tongjianyun_nutrition_rule" in content
	assert "线上规则未改变" in content
	assert "确认发布" in content
	assert "确认回滚" in content
	assert "禁止任何其他变量" in contract


def test_rule_requests_load_the_rule_management_skill() -> None:
	context = CodexBridge._oracle_skill_context("把维生素C的营养值计算方式改成保留率70%")
	assert "manage-tongjianyun-nutrition-rules" in context
	assert "retention_rate" in context
