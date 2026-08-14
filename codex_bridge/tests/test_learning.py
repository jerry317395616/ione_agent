from __future__ import annotations

import pytest
from app.identity import ToolIdentity
from app.learning import LearningStore


def proposal_payload() -> dict[str, str]:
	return {
		"category": "workflow",
		"trigger": "用户要求导入一周食谱并生成采购需求时",
		"proposed_rule": "先解析并展示歧义，确认后写入，再按就餐人数生成采购草稿。",
		"evidence": "用户两次纠正了先写入再核对的处理顺序。",
		"risk": "若跳过确认可能把菜品和食材对应错误。",
		"eval_prompt": "导入一份含合并单元格和别名食材的食谱并生成采购需求。",
		"expected_behavior": "先返回结构化预览和歧义，得到确认后才写入并回读。",
	}


def test_learning_requires_evaluation_and_only_approved_rules_enter_context(tmp_path) -> None:
	store = LearningStore(tmp_path / "learning.sqlite3")
	identity = ToolIdentity("owner@example.com", "Administrator", "child.example")
	proposal = store.propose(identity, proposal_payload())

	assert proposal["status"] == "pending"
	assert store.approved_context() == ""
	with pytest.raises(ValueError, match="only after evaluation passes"):
		store.review(
			proposal["proposal_id"],
			decision="approved",
			reviewer="Administrator",
			evaluation_status="failed",
		)

	review = store.review(
		proposal["proposal_id"],
		decision="approved",
		reviewer="Administrator",
		evaluation_status="passed",
		note="回归用例通过",
	)
	context = store.approved_context()

	assert review["status"] == "approved"
	assert "approved_site_learning" in context
	assert proposal_payload()["proposed_rule"] in context
	store.close()


def test_learning_deduplicates_and_rejects_personal_identifiers(tmp_path) -> None:
	store = LearningStore(tmp_path / "learning.sqlite3")
	identity = ToolIdentity("owner@example.com", "Administrator", "child.example")
	first = store.propose(identity, proposal_payload())
	second = store.propose(identity, proposal_payload())
	assert second == {
		"proposal_id": first["proposal_id"],
		"status": "duplicate",
		"message": "相同学习规则已经存在，未重复创建。",
	}

	sensitive = proposal_payload()
	sensitive["evidence"] = "家长邮箱 parent@example.com 提出这个要求"
	with pytest.raises(ValueError, match="personal identifiers"):
		store.propose(identity, sensitive)
	store.close()
