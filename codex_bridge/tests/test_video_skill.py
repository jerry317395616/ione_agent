from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "deal-materials-to-promo-video"


def test_skill_uses_controlled_deal_video_workflow() -> None:
	content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
	for tool in (
		"frappe_get_deal_video_sources",
		"frappe_upsert_deal_video",
		"frappe_submit_deal_video_render",
		"frappe_get_deal_video_render_status",
	):
		assert tool in content
	assert "Never generate React" in content
	assert "private" in content


def test_skill_manifest_contract_is_bounded() -> None:
	content = (SKILL_DIR / "references" / "video-manifest-contract.md").read_text(encoding="utf-8")
	for kind in ("cover", "context", "challenge", "solution", "capability", "roadmap", "value", "closing"):
		assert f"`{kind}`" in content
	assert "6-12 scenes" in content
	assert "30-180 seconds" in content
