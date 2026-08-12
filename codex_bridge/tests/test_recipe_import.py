from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.bridge import CodexBridge
from app.recipe_import import (
	_split_dishes,
	parse_recipe_attachment,
	preview_text,
	wants_recipe_commit,
)
from app.store import ConversationStore

SAMPLE = '''Attached document(s):
```md# "2026年6月第十七周食谱（定）.xlsx"
Sheet1:
临潼区幼儿园2026年6月22日—6月26日食谱（第十七周）,,,,,,
餐点,,星期一（6月22日）,星期二（6月23日）,星期三（6月24日）,星期四（6月25日）,星期五（6月26日）
早餐,主食品,纯牛奶  蓝莓切片面包 水煮鹌鹑蛋,绿豆小米粥 奶香小馒头 清炒银芽,麻酱卷 南瓜蔬菜粥,西蓝花虾仁蒸蛋 水果玉米,花生莲子粥 茄汁西葫芦 黑芝麻馒头
,带量,纯牛奶125g 切片面包54g 蓝莓果酱15.6g 鹌鹑蛋33g,绿豆5g 小米15g 面粉50g 胡萝卜10g 青椒10g 绿豆芽20g,面粉50g 芝麻酱4g 南瓜15g 江米10g 生菜8g,鸡蛋60g 西蓝花8g 虾仁10g 小葱2g 玉米60g,大米12g 花生米8g 莲子5g 西红柿25g 西葫芦50g 胡萝卜3g 面粉40g 黑芝麻3g
早点,主食品,香蕉,哈密瓜,乳瓜,圣女果,西瓜
,带量,100g,100g,50g,50g,100g
```
上传食谱'''


def test_parse_irregular_recipe_attachment() -> None:
	draft = parse_recipe_attachment(SAMPLE).as_dict()
	assert draft["recipe"]["recipeId"] == "2026-W17"
	assert draft["recipe"]["weekStart"] == "2026-06-22"
	assert draft["recipe"]["weekEnd"] == "2026-06-26"
	assert draft["stats"]["day_count"] == 5
	assert draft["stats"]["meal_count"] == 10
	assert draft["stats"]["dish_count"] >= 15
	assert draft["stats"]["ingredient_count"] == 33
	assert draft["stats"]["error_count"] == 0
	assert draft["days"][0]["portions"][0]["slot"] == "breakfast"
	assert any(
		row["ingredient"] == "纯牛奶" and row["amount"] == 125
		for row in draft["days"][0]["portions"][0]["dishIngredientRows"]
	)
	assert "尚未写入童健云" in preview_text(draft)


def test_detects_recipe_from_content_without_recipe_keyword_in_filename() -> None:
	from app.recipe_import import has_recipe_attachment

	text = SAMPLE.replace("2026年6月第十七周食谱（定）.xlsx", "幼儿园周菜单.xlsx").replace(
		"上传食谱", "请处理"
	)
	assert has_recipe_attachment(text)


def test_recipe_import_store_survives_next_turn(tmp_path: Path) -> None:
	draft = parse_recipe_attachment(SAMPLE).as_dict()
	store = ConversationStore(tmp_path / "state.sqlite3")
	store.save_recipe_import("user", "conversation", draft)
	loaded = store.latest_recipe_import("user", "conversation")
	assert loaded is not None
	assert loaded["task_id"] == draft["task_id"]
	assert loaded["status"] == "parsed"
	store.finish_recipe_import(draft["task_id"], status="committed", result={"name": "2026-W17"})
	loaded = store.latest_recipe_import("user", "conversation")
	assert loaded is not None
	assert loaded["status"] == "committed"
	assert loaded["commit_result"]["name"] == "2026-W17"
	store.close()


def test_commit_language_requires_explicit_confirmation_for_warning_flow() -> None:
	assert wants_recipe_commit("录入食谱")
	assert wants_recipe_commit("确认按当前解析结果录入食谱")
	assert not wants_recipe_commit("请预览食谱")


def test_real_compound_dish_names_are_split_only_at_dish_boundaries() -> None:
	assert _split_dishes("海苔碎蛋炒饭 虾皮冬瓜汤") == ["海苔碎蛋炒饭", "虾皮冬瓜汤"]
	assert _split_dishes("蔬菜麦饭 紫菜蛋花汤") == ["蔬菜麦饭", "紫菜蛋花汤"]
	assert _split_dishes("茄汁西葫芦黑芝麻馒头") == ["茄汁西葫芦", "黑芝麻馒头"]


def test_recipe_import_preview_then_confirmed_atomic_commit(tmp_path: Path) -> None:
	class FakeProxy:
		calls = 0

		async def call(self, tool, arguments, *, identity):
			self.calls += 1
			assert tool == "frappe_upsert_tongjianyun_recipe"
			assert identity.email == "owner@example.com"
			days = arguments["days"]
			result = {
				"name": arguments["recipe"]["recipeId"],
				"recipe_id": arguments["recipe"]["recipeId"],
				"title": arguments["recipe"]["title"],
				"day_count": len(days),
				"dish_count": sum(
					len(portion["dishes"]) for day in days for portion in day["portions"]
				),
				"ingredient_count": sum(
					len(portion["dishIngredientRows"])
					for day in days
					for portion in day["portions"]
				),
			}
			return {
				"success": True,
				"contentItems": [{"type": "inputText", "text": json.dumps(result)}],
			}

	proxy = FakeProxy()
	bridge = object.__new__(CodexBridge)
	bridge.settings = SimpleNamespace(
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_site_host="child.example",
		identity_audience="child.example",
	)
	bridge.app_server = SimpleNamespace(dynamic_tool_proxy=proxy)
	bridge.store = ConversationStore(tmp_path / "state.sqlite3")

	async def run_flow():
		common = {
			"user_id": "user",
			"conversation_id": "conversation",
			"manager_user_email": "owner@example.com",
			"manager_user_hint": "Administrator",
		}
		preview = await bridge._recipe_import_answer(SAMPLE, **common)
		assert "尚未写入童健云" in preview
		blocked = await bridge._recipe_import_answer("录入食谱", **common)
		assert "确认按当前解析结果" in blocked
		assert proxy.calls == 0
		committed = await bridge._recipe_import_answer("确认按当前解析结果录入食谱", **common)
		assert "食谱已准确录入童健云" in committed
		assert proxy.calls == 1

	asyncio.run(run_flow())
	bridge.store.close()
