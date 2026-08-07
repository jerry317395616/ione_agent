#!/usr/bin/env python3
"""Render a detailed Chinese business proposal as a dependency-free DOCX package."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CONTENT_WIDTH = 9026
INVALID_XML = re.compile(r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]")


def clean(value: Any) -> str:
	return INVALID_XML.sub("", str(value or "")).strip()


def xml_text(value: Any) -> str:
	return escape(clean(value), quote=True)


def run(text: Any, *, bold: bool = False, color: str | None = None, size: int | None = None) -> str:
	properties = []
	if bold:
		properties.append("<w:b/><w:bCs/>")
	if color:
		properties.append(f'<w:color w:val="{color}"/>')
	if size:
		properties.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
	parts = clean(text).split("\n")
	content = []
	for index, part in enumerate(parts):
		if index:
			content.append("<w:br/>")
		content.append(f'<w:t xml:space="preserve">{escape(part, quote=True)}</w:t>')
	return f"<w:r><w:rPr>{''.join(properties)}</w:rPr>{''.join(content)}</w:r>"


def paragraph(
	text: Any = "",
	*,
	style: str = "Normal",
	bold: bool = False,
	color: str | None = None,
	size: int | None = None,
	alignment: str | None = None,
	keep_next: bool = False,
	num_id: int | None = None,
	level: int = 0,
	indent: int | None = None,
	page_break_before: bool = False,
) -> str:
	properties = [f'<w:pStyle w:val="{style}"/>'] if style else []
	if alignment:
		properties.append(f'<w:jc w:val="{alignment}"/>')
	if keep_next:
		properties.append("<w:keepNext/>")
	if page_break_before:
		properties.append("<w:pageBreakBefore/>")
	if num_id is not None:
		properties.append(
			f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{num_id}"/></w:numPr>'
		)
	if indent is not None:
		properties.append(f'<w:ind w:left="{indent}"/>')
	return f"<w:p><w:pPr>{''.join(properties)}</w:pPr>{run(text, bold=bold, color=color, size=size)}</w:p>"


def page_break() -> str:
	return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def column_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
	weights = []
	for index, header in enumerate(headers):
		values = [header, *(row[index] if index < len(row) else "" for row in rows)]
		weight = max(7, min(30, max(len(clean(value)) for value in values)))
		weights.append(weight)
	total = sum(weights)
	widths = [round(CONTENT_WIDTH * weight / total) for weight in weights]
	widths[-1] += CONTENT_WIDTH - sum(widths)
	return widths


def table_block(table: dict[str, Any]) -> str:
	headers = [clean(value) for value in table.get("headers") or []]
	rows = [[clean(value) for value in row] for row in table.get("rows") or []]
	if not headers or not rows:
		return ""
	if any(len(row) != len(headers) for row in rows):
		raise ValueError(f"Table '{clean(table.get('title'))}' has inconsistent column counts")
	widths = column_widths(headers, rows)
	grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)

	def cell(value: str, width: int, *, header: bool = False) -> str:
		shade = '<w:shd w:val="clear" w:color="auto" w:fill="E8F0F7"/>' if header else ""
		text = paragraph(value, style="TableText", bold=header, color="16324F" if header else None)
		return (
			f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}'
			'<w:tcMar><w:top w:w="100" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
			'<w:bottom w:w="100" w:type="dxa"/><w:right w:w="120" w:type="dxa"/></w:tcMar>'
			f"</w:tcPr>{text}</w:tc>"
		)

	header_row = (
		'<w:tr><w:trPr><w:tblHeader/></w:trPr>'
		+ "".join(cell(value, widths[index], header=True) for index, value in enumerate(headers))
		+ "</w:tr>"
	)
	body_rows = "".join(
		"<w:tr>"
		+ "".join(cell(value, widths[index]) for index, value in enumerate(row))
		+ "</w:tr>"
		for row in rows
	)
	title = paragraph(table.get("title"), style="Caption", keep_next=True) if table.get("title") else ""
	return title + (
		'<w:tbl><w:tblPr><w:tblW w:w="9026" w:type="dxa"/><w:tblInd w:w="0" w:type="dxa"/>'
		'<w:tblLayout w:type="fixed"/><w:tblBorders>'
		'<w:top w:val="single" w:sz="6" w:color="B9C6D3"/>'
		'<w:left w:val="single" w:sz="6" w:color="B9C6D3"/>'
		'<w:bottom w:val="single" w:sz="6" w:color="B9C6D3"/>'
		'<w:right w:val="single" w:sz="6" w:color="B9C6D3"/>'
		'<w:insideH w:val="single" w:sz="4" w:color="D7E0E8"/>'
		'<w:insideV w:val="single" w:sz="4" w:color="D7E0E8"/>'
		f"</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>{header_row}{body_rows}</w:tbl>"
		'<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>'
	)


def metadata_table(payload: dict[str, Any]) -> str:
	rows = [
		("客户", payload["customer_name"]),
		("来源线索", payload["lead_name"]),
		("编制单位", payload["prepared_by"]),
		("日期", payload["date"]),
		("版本", payload["version"]),
		("密级", payload["confidentiality"]),
	]
	return table_block({"headers": ["项目", "内容"], "rows": rows})


def validate_payload(payload: dict[str, Any]) -> None:
	required = (
		"proposal_title",
		"customer_name",
		"lead_name",
		"prepared_by",
		"date",
		"version",
		"confidentiality",
	)
	missing = [field for field in required if not clean(payload.get(field))]
	if missing:
		raise ValueError(f"Missing required proposal fields: {', '.join(missing)}")
	if not isinstance(payload.get("executive_summary"), list) or not payload["executive_summary"]:
		raise ValueError("executive_summary must be a non-empty list")
	sections = payload.get("sections")
	if not isinstance(sections, list) or len(sections) < 10:
		raise ValueError("A detailed proposal requires at least 10 sections")
	for index, section in enumerate(sections, start=1):
		if not isinstance(section, dict) or not clean(section.get("title")):
			raise ValueError(f"Section {index} requires a title")
		if not any(section.get(key) for key in ("paragraphs", "bullets", "tables")):
			raise ValueError(f"Section {index} requires paragraphs, bullets or tables")
	for table in (
		table
		for section in sections
		for table in (section.get("tables") or [])
	):
		if not isinstance(table, dict) or not table.get("headers") or not table.get("rows"):
			raise ValueError("Each table requires headers and rows")
	content = json.dumps(payload, ensure_ascii=False)
	if len(re.sub(r"\s+", "", content)) < 1200:
		raise ValueError("Proposal content is too short; provide at least 1,200 non-whitespace characters")


def document_xml(payload: dict[str, Any]) -> str:
	body = [
		paragraph(payload["confidentiality"], style="Subtitle", color="567086", alignment="right"),
		paragraph("I-ONE AI", style="Subtitle", color="1F6E8C", bold=True, alignment="center"),
		paragraph(payload["proposal_title"], style="Title", alignment="center"),
		paragraph(payload["customer_name"], style="Subtitle", alignment="center"),
		paragraph("基于客户需求的解决方案建议书", style="Subtitle", color="567086", alignment="center"),
		paragraph("", style="Normal"),
		metadata_table(payload),
		paragraph("本方案依据当前线索资料编制，未确认内容均以方案假设或待确认事项标识。", style="Caption"),
		page_break(),
		paragraph("方案摘要", style="Heading1", keep_next=True),
	]
	for summary in payload["executive_summary"]:
		body.append(paragraph(summary))
	body.extend(
		[
			paragraph("目录", style="Heading1", keep_next=True),
			*(paragraph(f"{index}. {section['title']}", indent=240) for index, section in enumerate(payload["sections"], 1)),
			page_break(),
		]
	)
	for index, section in enumerate(payload["sections"], start=1):
		body.append(paragraph(f"{index}. {section['title']}", style="Heading1", keep_next=True))
		for item in section.get("paragraphs") or []:
			body.append(paragraph(item))
		for item in section.get("bullets") or []:
			body.append(paragraph(item, style="ListParagraph", num_id=1))
		for table in section.get("tables") or []:
			body.append(table_block(table))
	if payload.get("sources"):
		body.append(paragraph("资料依据", style="Heading1", keep_next=True))
		for source in payload["sources"]:
			body.append(paragraph(source, style="ListParagraph", num_id=1))
	body.append(
		'<w:sectPr><w:headerReference w:type="default" r:id="rId4"/>'
		'<w:footerReference w:type="default" r:id="rId5"/>'
		'<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" '
		'w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'
		'<w:cols w:space="720"/><w:docGrid w:linePitch="312"/></w:sectPr>'
	)
	return (
		'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
		f'<w:document xmlns:w="{WORD_NS}" xmlns:r="{REL_NS}"><w:body>{"".join(body)}</w:body></w:document>'
	)


def styles_xml() -> str:
	return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{WORD_NS}">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei" w:cs="Arial"/><w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="zh-CN" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="360" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr></w:pPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:pPr><w:widowControl/><w:spacing w:after="160" w:line="360" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="1440" w:after="360"/><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="16324F"/><w:sz w:val="52"/><w:szCs w:val="52"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="180"/><w:jc w:val="center"/></w:pPr><w:rPr><w:color w:val="567086"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="360" w:after="160"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="16324F"/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="260" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="1F6E8C"/><w:sz w:val="25"/><w:szCs w:val="25"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="540" w:hanging="240"/><w:spacing w:after="100"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="Caption"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="120" w:after="80"/></w:pPr><w:rPr><w:b/><w:bCs/><w:color w:val="567086"/><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="0" w:after="0" w:line="300" w:lineRule="auto"/><w:jc w:val="left"/></w:pPr><w:rPr><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>
</w:styles>'''


def numbering_xml() -> str:
	return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="{WORD_NS}">
  <w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="multilevel"/>
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="540"/></w:tabs><w:ind w:left="540" w:hanging="240"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:hint="default"/></w:rPr></w:lvl>
  </w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>'''


def package_parts(payload: dict[str, Any]) -> dict[str, str]:
	now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
	return {
		"[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>''',
		"_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>''',
		"word/document.xml": document_xml(payload),
		"word/styles.xml": styles_xml(),
		"word/numbering.xml": numbering_xml(),
		"word/settings.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="{WORD_NS}"><w:zoom w:percent="100"/><w:updateFields w:val="true"/><w:defaultTabStop w:val="720"/><w:compat/></w:settings>''',
		"word/header1.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="{WORD_NS}" xmlns:r="{REL_NS}">{paragraph("I-ONE AI  |  " + clean(payload["proposal_title"]), style="Caption", color="567086")}</w:hdr>''',
		"word/footer1.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="{WORD_NS}" xmlns:r="{REL_NS}"><w:p><w:pPr><w:jc w:val="center"/></w:pPr>{run(clean(payload["confidentiality"]) + "  |  ", color="73879A", size=17)}<w:r><w:rPr><w:color w:val="73879A"/><w:sz w:val="17"/></w:rPr><w:fldChar w:fldCharType="begin"/><w:instrText xml:space="preserve"> PAGE </w:instrText><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>''',
		"word/_rels/document.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>''',
		"docProps/core.xml": f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{xml_text(payload["proposal_title"])}</dc:title><dc:subject>{xml_text(payload["customer_name"])}</dc:subject><dc:creator>{xml_text(payload["prepared_by"])}</dc:creator><cp:lastModifiedBy>{xml_text(payload["prepared_by"])}</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>''',
		"docProps/app.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>I-ONE AI</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><Company>I-ONE</Company><AppVersion>1.0</AppVersion></Properties>''',
	}


def create_docx(payload: dict[str, Any], output_path: Path) -> None:
	validate_payload(payload)
	if output_path.suffix.lower() != ".docx":
		raise ValueError("Output file must end in .docx")
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
		for name, content in package_parts(payload).items():
			archive.writestr(name, content.encode("utf-8"))
	with zipfile.ZipFile(output_path) as archive:
		if archive.testzip() is not None:
			raise ValueError("Generated DOCX package failed integrity validation")
		if not {"[Content_Types].xml", "word/document.xml"}.issubset(archive.namelist()):
			raise ValueError("Generated DOCX package is incomplete")


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--input", required=True, type=Path, help="UTF-8 proposal JSON file")
	parser.add_argument("--output", required=True, type=Path, help="Output .docx path")
	args = parser.parse_args()
	payload = json.loads(args.input.read_text(encoding="utf-8"))
	create_docx(payload, args.output)
	print(json.dumps({"file": str(args.output.resolve()), "bytes": args.output.stat().st_size}, ensure_ascii=False))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
