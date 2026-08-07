from __future__ import annotations

import json
import zipfile
from importlib import util
from pathlib import Path

import pytest

SCRIPT = (
	Path(__file__).resolve().parents[1]
	/ "skills"
	/ "lead-proposal-to-deal"
	/ "scripts"
	/ "render_proposal.py"
)


def load_renderer():
	spec = util.spec_from_file_location("render_proposal", SCRIPT)
	module = util.module_from_spec(spec)
	assert spec.loader
	spec.loader.exec_module(module)
	return module


def proposal_payload() -> dict:
	sections = []
	for index in range(1, 11):
		sections.append(
			{
				"title": f"第 {index} 章",
				"paragraphs": ["这是经过核验的客户需求说明。" * 12],
				"bullets": ["方案假设：具体范围需在项目启动阶段确认。"],
				"tables": [
					{
						"title": "交付检查",
						"headers": ["事项", "响应", "验证"],
						"rows": [["需求", "方案", "验收"]],
					}
				],
			}
		)
	return {
		"proposal_title": "医疗行业客户项目建设方案",
		"customer_name": "示例客户",
		"lead_name": "CRM-LEAD-2026-00011",
		"prepared_by": "I-ONE AI",
		"date": "2026-08-07",
		"version": "V1.0",
		"confidentiality": "商业机密，仅供项目沟通使用",
		"executive_summary": ["本方案依据线索资料形成。"],
		"sections": sections,
		"sources": ["CRM Lead: CRM-LEAD-2026-00011"],
	}


def test_renderer_creates_valid_docx(tmp_path) -> None:
	renderer = load_renderer()
	output = tmp_path / "proposal.docx"
	renderer.create_docx(proposal_payload(), output)

	with zipfile.ZipFile(output) as archive:
		assert archive.testzip() is None
		assert "word/document.xml" in archive.namelist()
		document = archive.read("word/document.xml").decode("utf-8")
		assert "医疗行业客户项目建设方案" in document
		assert "CRM-LEAD-2026-00011" in document
		assert "<w:tbl>" in document


def test_renderer_rejects_shallow_proposal(tmp_path) -> None:
	renderer = load_renderer()
	payload = proposal_payload()
	payload["sections"] = payload["sections"][:2]
	with pytest.raises(ValueError, match="at least 10 sections"):
		renderer.create_docx(payload, tmp_path / "proposal.docx")


def test_schema_example_is_json_serializable() -> None:
	json.dumps(proposal_payload(), ensure_ascii=False)
