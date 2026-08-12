from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any

ATTACHMENT_PATTERN = re.compile(
	r"#\s*[\"“](?P<name>[^\"”]+\.(?:xlsx|xls|csv))[\"”]\s*(?P<body>.*?)(?:```|\Z)",
	re.IGNORECASE | re.DOTALL,
)
YEAR_PATTERN = re.compile(r"(?P<year>20\d{2})\s*年")
MONTH_DAY_PATTERN = re.compile(r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日")
AMOUNT_PATTERN = re.compile(
	r"(?P<name>[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9（）()·\-/ ]*?)"
	r"\s*(?P<amount>\d+(?:\.\d+)?)\s*"
	r"(?P<unit>公斤|千克|毫升|克|kg|ml|g|l|升|个|只|枚|盒|块|片|份|勺)",
	re.IGNORECASE,
)
AMOUNT_ONLY_PATTERN = re.compile(
	r"^\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>公斤|千克|毫升|克|kg|ml|g|l|升|个|只|枚|盒|块|片|份|勺)\s*$",
	re.IGNORECASE,
)

MEAL_ALIASES = {
	"早餐": ("breakfast", "早餐"),
	"早饭": ("breakfast", "早餐"),
	"早点": ("morningSnack", "早点"),
	"早加餐": ("morningSnack", "早点"),
	"上午点": ("morningSnack", "早点"),
	"午餐": ("lunch", "午餐"),
	"午饭": ("lunch", "午餐"),
	"午点": ("snack", "午点"),
	"下午点": ("snack", "午点"),
	"下午加餐": ("snack", "午点"),
	"晚餐": ("dinner", "晚餐"),
	"晚饭": ("dinner", "晚餐"),
}
AMOUNT_LABELS = {"带量", "用量", "每人用量", "每人份量", "净用量", "重量", "克重"}
DISH_LABELS = {"主食品", "主食", "配餐", "副食", "菜品", "饮品", "水果", "点心", "汤品"}
UNIT_MAP = {
	"克": "g",
	"g": "g",
	"公斤": "kg",
	"千克": "kg",
	"kg": "kg",
	"毫升": "ml",
	"ml": "ml",
	"l": "l",
	"升": "l",
}
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
COOKING_WORDS = (
	"清炒",
	"水煮",
	"蒸",
	"炒",
	"烧",
	"拌",
	"煮",
	"奶香",
	"茄汁",
	"麻酱",
	"养胃",
	"蔬菜",
	"甜心",
	"纯",
)
INTERNAL_DISH_SUFFIXES = (
	"鹌鹑蛋",
	"小馒头",
	"西葫芦",
	"鸡蛋汤",
	"银耳汤",
	"豆腐汤",
	"海带汤",
	"馒头",
	"面包",
	"酸奶",
	"豆浆",
	"玉米",
	"鸡蛋",
	"麦饭",
	"米饭",
	"炒饭",
	"面",
	"粥",
	"汤",
	"饼",
	"卷",
	"糕",
	"糁",
)


@dataclass(frozen=True)
class ParseIssue:
	severity: str
	code: str
	message: str
	location: str = ""


@dataclass(frozen=True)
class RecipeImportDraft:
	task_id: str
	source_file_name: str
	source_sha256: str
	recipe: dict[str, Any]
	days: list[dict[str, Any]]
	issues: list[dict[str, str]]
	stats: dict[str, Any]
	confidence: int

	def as_dict(self) -> dict[str, Any]:
		return asdict(self)


def _clean(value: Any) -> str:
	return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def _compact(value: Any) -> str:
	return re.sub(r"[\s:：()（）]+", "", str(value or "")).strip()


def _csv_rows(body: str) -> list[list[str]]:
	body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
	body = re.sub(r"^Sheet[^:\n]{0,80}:\s*\n", "", body, flags=re.IGNORECASE)
	reader = csv.reader(io.StringIO(body))
	rows = [[_clean(cell) for cell in row] for row in reader]
	return [row for row in rows if any(row)]


def _document(text: str) -> tuple[str, str]:
	match = ATTACHMENT_PATTERN.search(text)
	if not match:
		raise ValueError("没有找到可解析的 Excel 或 CSV 食谱附件内容")
	return _clean(match.group("name")), match.group("body")


def has_recipe_attachment(text: str) -> bool:
	match = ATTACHMENT_PATTERN.search(text)
	if not match:
		return False
	content = _compact(f"{match.group('name')} {match.group('body')} {text[-200:]}")
	meal_hits = sum(alias in content for alias in ("早餐", "早点", "午餐", "午点", "晚餐"))
	return "食谱" in content or "菜单" in content or meal_hits >= 2


def wants_recipe_commit(text: str) -> bool:
	if has_recipe_attachment(text):
		return False
	value = _compact(text)
	return bool(
		re.search(r"(?:确认|按当前结果)?(?:录入|导入|保存)(?:这份|当前)?食谱", value)
		or value in {"确认", "确认录入", "确认导入", "确认保存"}
	)


def wants_recipe_preview(text: str) -> bool:
	value = _compact(text)
	return any(token in value for token in ("食谱预览", "解析结果", "导入状态", "查看食谱"))


def _extract_year(rows: list[list[str]]) -> int:
	for row in rows[:6]:
		match = YEAR_PATTERN.search(" ".join(row))
		if match:
			return int(match.group("year"))
	return date.today().year


def _header(rows: list[list[str]]) -> tuple[int, list[tuple[int, str]]]:
	for row_index, row in enumerate(rows):
		columns = [
			(index, value)
			for index, value in enumerate(row)
			if MONTH_DAY_PATTERN.search(value) or any(day in value for day in WEEKDAYS)
		]
		if len(columns) >= 2:
			return row_index, columns
	raise ValueError("无法识别食谱中的星期和日期表头")


def _iso_date(value: str, year: int) -> str:
	match = MONTH_DAY_PATTERN.search(value)
	if not match:
		return ""
	try:
		return date(year, int(match.group("month")), int(match.group("day"))).isoformat()
	except ValueError:
		return ""


def _weekday(value: str, index: int) -> str:
	for weekday in WEEKDAYS:
		if weekday in value:
			return weekday
	return WEEKDAYS[index] if index < len(WEEKDAYS) else f"第{index + 1}天"


def _split_compound_dish(value: str) -> list[str]:
	value = value.strip(" 、，,；;+/\t")
	if not value:
		return []
	for suffix in INTERNAL_DISH_SUFFIXES:
		start = 0
		while True:
			at = value.find(suffix, start)
			if at < 0:
				break
			boundary = at + len(suffix)
			if 2 <= boundary <= len(value) - 2:
				left = value[:boundary].strip()
				right = value[boundary:].strip()
				if left and right:
					return [left, *_split_compound_dish(right)]
			start = boundary
	return [value]


def _split_dishes(value: str) -> list[str]:
	parts = re.split(r"\s+|[、，,；;+/]+", _clean(value))
	result: list[str] = []
	for part in parts:
		for dish in _split_compound_dish(part):
			dish = dish.strip(" -—")
			if len(dish) >= 2 and dish not in result:
				result.append(dish)
	return result


def _parse_ingredients(value: str) -> list[dict[str, Any]]:
	result: list[dict[str, Any]] = []
	for match in AMOUNT_PATTERN.finditer(_clean(value)):
		name = _clean(match.group("name")).strip("、，,；;+/ -")
		if not name:
			continue
		unit = match.group("unit").lower()
		result.append(
			{
				"ingredient": name,
				"amount": float(match.group("amount")),
				"unit": UNIT_MAP.get(unit, unit),
			}
		)
	return result


def _name_key(value: str) -> str:
	key = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]", "", value).lower()
	for token in COOKING_WORDS:
		key = key.replace(token, "")
	return key


def _dish_score(ingredient: str, dish: str) -> float:
	left = _name_key(ingredient)
	right = _name_key(dish)
	if not left or not right:
		return 0.0
	if left in right or right in left:
		return 1.0
	overlap = len(set(left) & set(right)) / max(1, len(set(left)))
	sequence = SequenceMatcher(None, left, right).ratio()
	return max(overlap * 0.85, sequence * 0.75)


def _link_ingredients(
	dishes: list[str],
	ingredients: list[dict[str, Any]],
	*,
	location: str,
	issues: list[ParseIssue],
) -> list[dict[str, Any]]:
	if not ingredients:
		return []
	if not dishes:
		issues.append(ParseIssue("error", "missing_dish", "识别到带量，但没有识别到对应菜品", location))
		return []
	if len(dishes) == 1:
		return [{**row, "dishName": dishes[0]} for row in ingredients]

	linked: list[dict[str, Any]] = []
	ambiguous: list[str] = []
	last_index = 0
	for row_index, row in enumerate(ingredients):
		scores = [_dish_score(str(row["ingredient"]), dish) for dish in dishes]
		best_index = max(range(len(dishes)), key=scores.__getitem__)
		best_score = scores[best_index]
		if best_score < 0.24:
			position_index = min(len(dishes) - 1, int(row_index * len(dishes) / len(ingredients)))
			best_index = max(last_index, position_index)
			if not _position_group_is_unambiguous(dishes, ingredients, row_index, best_index):
				ambiguous.append(str(row["ingredient"]))
		last_index = max(last_index, best_index)
		linked.append({**row, "dishName": dishes[best_index]})
	if ambiguous:
		preview = "、".join(ambiguous[:8])
		issues.append(
			ParseIssue(
				"warning",
				"ambiguous_ingredient_link",
				f"部分食材在原表中未注明所属菜品，已按原始顺序关联：{preview}",
				location,
			)
		)
	assigned_dishes = {str(row["dishName"]) for row in linked}
	missing = [dish for dish in dishes if dish not in assigned_dishes]
	if missing:
		issues.append(
			ParseIssue(
				"warning",
				"dish_without_ingredient",
				f"菜品没有识别到对应带量食材：{'、'.join(missing)}",
				location,
			)
		)
	return linked


def _position_group_is_unambiguous(
	dishes: list[str],
	ingredients: list[dict[str, Any]],
	row_index: int,
	group_index: int,
) -> bool:
	"""Accept ordered groups only when each dish has an identifiable anchor ingredient."""

	group_start = int(group_index * len(ingredients) / len(dishes))
	group_end = int((group_index + 1) * len(ingredients) / len(dishes))
	if not (group_start <= row_index < max(group_start + 1, group_end)):
		return False
	return any(
		_dish_score(str(ingredients[index]["ingredient"]), dishes[group_index]) >= 0.45
		for index in range(group_start, min(len(ingredients), max(group_start + 1, group_end)))
	)


def _chinese_number(value: str) -> int | None:
	if value.isdigit():
		return int(value)
	digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
	if value == "十":
		return 10
	if "十" in value:
		left, right = value.split("十", 1)
		return digits.get(left, 1) * 10 + digits.get(right, 0)
	if value in digits:
		return digits[value]
	return None


def _recipe_id(title: str, week_start: str, source_hash: str) -> str:
	match = re.search(r"第\s*([零一二两三四五六七八九十\d]+)\s*周", title)
	week = _chinese_number(match.group(1)) if match else None
	if week and week_start:
		return f"{week_start[:4]}-W{week:02d}"
	if week_start:
		return f"RECIPE-{week_start.replace('-', '')}"
	return f"RECIPE-{source_hash[:10].upper()}"


def parse_recipe_attachment(text: str) -> RecipeImportDraft:
	file_name, body = _document(text)
	rows = _csv_rows(body)
	if not rows:
		raise ValueError("附件中没有可解析的食谱数据")
	source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
	year = _extract_year(rows)
	header_index, day_columns = _header(rows)
	title = next((_clean(" ".join(row)) for row in rows[:header_index] if any(row)), "")
	issues: list[ParseIssue] = []
	days: list[dict[str, Any]] = []
	for index, (_, heading) in enumerate(day_columns):
		meal_date = _iso_date(heading, year)
		if not meal_date:
			issues.append(
				ParseIssue("error", "invalid_date", f"无法识别日期：{heading}", f"第{index + 1}个日期列")
			)
		days.append(
			{
				"id": meal_date or f"DAY-{index + 1}",
				"date": meal_date or None,
				"day": _weekday(heading, index),
				"portions": [],
			}
		)

	portion_maps: list[dict[str, dict[str, Any]]] = [{} for _ in days]
	current_meal: tuple[str, str] | None = None
	first_day_column = min(column_index for column_index, _ in day_columns)
	for row_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
		row_headers = [_compact(value) for value in row[:first_day_column] if _compact(value)]
		meal_header = next((value for value in row_headers if value in MEAL_ALIASES), "")
		if meal_header:
			current_meal = MEAL_ALIASES[meal_header]
		if current_meal is None:
			continue
		row_label = next(
			(value for value in row_headers if value not in MEAL_ALIASES),
			"",
		)
		is_amount = row_label in AMOUNT_LABELS or "带量" in row_label or "用量" in row_label
		is_dish = row_label in DISH_LABELS or not row_label
		if not is_amount and not is_dish:
			is_dish = True

		for day_index, (column_index, _) in enumerate(day_columns):
			value = _clean(row[column_index] if column_index < len(row) else "")
			if not value:
				continue
			slot, label = current_meal
			portion = portion_maps[day_index].setdefault(
				slot,
				{
					"slot": slot,
					"label": label,
					"dishes": [],
					"amountPerChild": "",
					"totalAmount": "",
					"dishIngredientRows": [],
					"_raw_ingredients": [],
				},
			)
			if is_amount:
				portion["amountPerChild"] = " ".join(filter(None, (portion["amountPerChild"], value)))
				parsed = _parse_ingredients(value)
				amount_only = AMOUNT_ONLY_PATTERN.fullmatch(value)
				if not parsed and amount_only and len(portion["dishes"]) == 1:
					unit = amount_only.group("unit").lower()
					parsed = [
						{
							"ingredient": portion["dishes"][0],
							"amount": float(amount_only.group("amount")),
							"unit": UNIT_MAP.get(unit, unit),
						}
					]
				if not parsed:
					issues.append(
						ParseIssue(
							"warning",
							"unparsed_amount",
							f"带量内容无法拆分为食材、数量和单位：{value[:80]}",
							f"{days[day_index]['day']} {label} 第{row_number}行",
						)
					)
				portion["_raw_ingredients"].extend(parsed)
			else:
				for dish in _split_dishes(value):
					if dish not in portion["dishes"]:
						portion["dishes"].append(dish)

	meal_slots = tuple(dict.fromkeys(MEAL_ALIASES.values()))
	for day_index, day in enumerate(days):
		for slot, _ in meal_slots:
			portion = portion_maps[day_index].get(slot)
			if not portion:
				continue
			location = f"{day['day']} {portion['label']}"
			portion["dishIngredientRows"] = _link_ingredients(
				portion["dishes"],
				portion.pop("_raw_ingredients"),
				location=location,
				issues=issues,
			)
			day[slot] = "、".join(portion["dishes"])
			day["portions"].append(portion)
		if not day["portions"]:
			issues.append(ParseIssue("error", "empty_day", "该日期没有识别到任何餐次", str(day["day"])))

	week_dates = [str(day.get("date") or "") for day in days if day.get("date")]
	week_start = min(week_dates) if week_dates else ""
	week_end = max(week_dates) if week_dates else ""
	recipe = {
		"recipeId": _recipe_id(title, week_start, source_hash),
		"title": title or f"{week_start or '未定日期'}食谱",
		"weekStart": week_start or None,
		"weekEnd": week_end or None,
		"sourceFileName": file_name,
		"parser": "ione-agent-recipe-import-v1",
		"relationSource": "deterministic+validated-sequence",
	}
	meal_count = sum(len(day["portions"]) for day in days)
	dish_count = sum(len(portion["dishes"]) for day in days for portion in day["portions"])
	ingredient_count = sum(
		len(portion["dishIngredientRows"]) for day in days for portion in day["portions"]
	)
	error_count = sum(issue.severity == "error" for issue in issues)
	warning_count = sum(issue.severity == "warning" for issue in issues)
	confidence = max(0, min(100, 100 - error_count * 30 - min(30, warning_count * 2)))
	stats = {
		"day_count": len(days),
		"meal_count": meal_count,
		"dish_count": dish_count,
		"ingredient_count": ingredient_count,
		"error_count": error_count,
		"warning_count": warning_count,
		"week_start": week_start,
		"week_end": week_end,
	}
	return RecipeImportDraft(
		task_id=f"RIMP-{uuid.uuid4().hex[:12].upper()}",
		source_file_name=file_name,
		source_sha256=source_hash,
		recipe=recipe,
		days=days,
		issues=[asdict(issue) for issue in issues],
		stats=stats,
		confidence=confidence,
	)


def preview_text(draft: dict[str, Any]) -> str:
	stats = draft.get("stats") or {}
	recipe = draft.get("recipe") or {}
	issues = draft.get("issues") or []
	lines = [
		"食谱附件已完成确定性解析，目前尚未写入童健云。",
		"",
		f"- 导入任务：{draft.get('task_id')}",
		f"- 文件：{draft.get('source_file_name')}",
		f"- 食谱：{recipe.get('title')}",
		f"- 日期：{stats.get('week_start') or '未识别'} 至 {stats.get('week_end') or '未识别'}",
		f"- 解析结果：{stats.get('day_count', 0)} 天、{stats.get('meal_count', 0)} 餐次、"
		f"{stats.get('dish_count', 0)} 道菜、{stats.get('ingredient_count', 0)} 条食材明细",
		f"- 解析置信度：{draft.get('confidence', 0)}%",
	]
	days = draft.get("days") or []
	if days:
		lines.extend(("", "菜品预览："))
		for day in days[:7]:
			meals = []
			for portion in day.get("portions") or []:
				dishes = "、".join(str(value) for value in (portion.get("dishes") or []))
				if dishes:
					meals.append(f"{portion.get('label')}：{dishes}")
			lines.append(f"- {day.get('day')}（{day.get('date') or '日期未识别'}）：{'；'.join(meals)}")
	if issues:
		lines.extend(("", "需要注意："))
		for issue in issues[:8]:
			prefix = "错误" if issue.get("severity") == "error" else "提示"
			location = f"（{issue.get('location')}）" if issue.get("location") else ""
			lines.append(f"- {prefix}{location}：{issue.get('message')}")
		if len(issues) > 8:
			lines.append(f"- 另有 {len(issues) - 8} 条解析提示。")
	if int(stats.get("error_count") or 0):
		lines.extend(("", "存在阻断错误，暂不能写入。请修正原文件后重新上传。"))
	elif int(stats.get("warning_count") or 0):
		lines.extend(("", "原表存在菜品与食材关系不明确的内容。核对后回复“确认按当前解析结果录入食谱”。"))
	else:
		lines.extend(("", "核对无误后回复“录入食谱”，系统会原子写入并回读验证。"))
	return "\n".join(lines)


def result_payload(draft: dict[str, Any]) -> dict[str, Any]:
	return {"recipe": draft["recipe"], "days": draft["days"]}


def decode_tool_result(result: dict[str, Any]) -> dict[str, Any]:
	for item in result.get("contentItems") or []:
		if not isinstance(item, dict):
			continue
		value = str(item.get("text") or "").strip()
		try:
			parsed = json.loads(value)
		except ValueError:
			continue
		if isinstance(parsed, dict):
			return parsed.get("result") if isinstance(parsed.get("result"), dict) else parsed
	return {}
