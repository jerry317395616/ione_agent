from pathlib import Path

SKILL_DIR = (
	Path(__file__).resolve().parents[1]
	/ "skills"
	/ "analyze-tongjianyun-recipe"
)


def test_existing_recipe_analysis_skill_uses_deterministic_report_tool() -> None:
	content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

	assert "frappe_list_documents" in content
	assert "frappe_get_document" in content
	assert "frappe_generate_tongjianyun_recipe_analysis" in content
	assert "不要让模型自行估算" in content
	assert "download_url" in content


def test_existing_recipe_analysis_skill_documents_tool_contract() -> None:
	contract = (SKILL_DIR / "references" / "analysis-contract.md").read_text(
		encoding="utf-8"
	)

	assert '"doctype": "Tongjianyun Recipe"' in contract
	assert '"is_deleted": 0' in contract
	assert '"recipe_name": "2026-W17"' in contract
	assert "食物名称是动态的" in contract


def test_existing_recipe_analysis_skill_has_agent_metadata() -> None:
	metadata = (SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8")

	assert 'display_name: "食谱分析"' in metadata
	assert "$analyze-tongjianyun-recipe" in metadata
