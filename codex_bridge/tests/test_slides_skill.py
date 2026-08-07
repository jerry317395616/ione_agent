from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "deal-proposal-to-slides"


def test_skill_defines_verified_deal_to_slides_workflow() -> None:
	content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
	assert "frappe_read_word_attachment" in content
	assert "frappe_upsert_deal_presentation" in content
	assert "private by default" in content
	assert "Never create a second presentation" in content


def test_skill_includes_slide_contract() -> None:
	reference = (SKILL_DIR / "references" / "presentation-contract.md").read_text(encoding="utf-8")
	for layout in ("cover", "content", "metrics", "timeline", "closing"):
		assert f"`{layout}`" in reference
