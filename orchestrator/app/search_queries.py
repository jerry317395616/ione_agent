from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

GENERIC_KEYWORDS = {"招标", "采购", "公告", "中标", "医院", "项目"}
ALL_REGIONS = {"全国", "全国范围", "不限", "中国"}


def build_search_queries(criteria: dict[str, Any], sources: list[dict[str, Any]]) -> list[str]:
	regions = [
		str(region).strip()
		for region in (criteria.get("regions") or [""])
		if str(region).strip() not in ALL_REGIONS
	] or [""]
	keywords = [
		str(keyword).strip()
		for keyword in (criteria.get("keywords") or [])
		if str(keyword).strip() and str(keyword).strip() not in GENERIC_KEYWORDS
	]
	industries = [
		part.strip()
		for part in re.split(r"[/,，、|]", str(criteria.get("industry") or ""))
		if part.strip()
	]
	terms = keywords[:8] or industries[:4] or ["医疗信息化"]
	queries: list[str] = []

	for region in regions[:6]:
		for term in terms:
			queries.append(" ".join(part for part in (region, term, "招标 公告") if part))
		for industry in industries[:3]:
			queries.append(" ".join(part for part in (region, industry, "采购 公告") if part))

	primary_term = terms[0]
	for source in sources[:8]:
		domain = urlparse(str(source.get("base_url") or "")).hostname
		if domain:
			queries.append(f"site:{domain} {primary_term} 招标 公告")

	return list(dict.fromkeys(query for query in queries if query.strip()))
