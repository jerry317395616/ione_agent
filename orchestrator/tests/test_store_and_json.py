from pathlib import Path

from app.clients import DeepSeekClient, parse_json
from app.contracts import AgentDecision, AgentToolCall
from app.search_queries import build_search_queries
from app.settings import Settings
from app.store import RunStore
from app.workflow import LeadWorkflow, ReviewCandidatesArguments


def test_parse_json_accepts_fenced_payload():
	assert parse_json('result\n```json\n{"intent":"lead_discovery"}\n```', {}) == {"intent": "lead_discovery"}


def test_deepseek_extract_accepts_web_relay_reply():
	assert DeepSeekClient._extract({"status": "completed", "reply": "[{\"fingerprint\": \"abc\"}]"}) == '[{"fingerprint": "abc"}]'
	assert DeepSeekClient._job_payload({"ok": True, "job": {"id": "job-1", "status": "queued"}}) == {
		"id": "job-1",
		"status": "queued",
	}


def test_deepseek_keeps_markdown_review_as_fallback_plan():
	plans = DeepSeekClient._parse_review_content("## 跟进方案\n1. 核验公告与资质")
	assert plans == [{"deepseek_plan": "## 跟进方案\n1. 核验公告与资质"}]
	first = DeepSeekClient._conversation_key()
	second = DeepSeekClient._conversation_key()
	assert first.startswith("ione-agent-lead-")
	assert first != second
	parsed = DeepSeekClient._parse_review_content(
		'[{"fingerprint":"abc","deepseek_plan":["核验公告", "准备资质"]}]'
	)
	assert parsed[0]["deepseek_plan"] == "- 核验公告\n- 准备资质"


def test_search_queries_are_short_and_drop_all_region_and_slashes():
	queries = build_search_queries(
		{
			"industry": "医疗健康/人工智能",
			"regions": ["全国"],
			"keywords": ["医院", "AI辅助诊疗", "医保智能审核", "招标"],
		},
		[],
	)
	assert "AI辅助诊疗 招标 公告" in queries
	assert "医保智能审核 招标 公告" in queries
	assert all("全国" not in query and "/" not in query for query in queries)


def test_store_is_idempotent_and_persists_result(tmp_path: Path):
	store = RunStore(tmp_path / "runs.sqlite3")
	payload = {"client_run_id": "RUN-1", "task_id": "TASK-1", "user_id": "u", "request": "找线索"}
	first = store.create(payload)
	second = store.create(payload)
	assert first["run_id"] == second["run_id"]
	store.update(first["run_id"], status="completed", result={"candidates": [{"title": "A"}]})
	assert store.get(first["run_id"])["result"]["candidates"][0]["title"] == "A"


def test_fallback_analysis_preserves_verified_evidence():
	items = LeadWorkflow._fallback_analysis(
		[
			{
				"title": "医院信息化建设公开招标公告",
				"source_url": "https://www.ccgp.gov.cn/tender/1",
				"raw_text": "某医院采购医保智能审核系统",
				"evidence": [{"url": "https://www.ccgp.gov.cn/tender/1", "snippet": "公告"}],
			}
		],
		{"industry": "医疗信息化", "keywords": ["医保智能审核"]},
	)
	assert items[0]["source_url"].startswith("https://www.ccgp.gov.cn")
	assert items[0]["evidence"]
	assert items[0]["relevance_score"] >= 70
	assert items[0]["risk_level"] == "中"


def test_fallback_review_plan_uses_only_candidate_facts():
	plan = LeadWorkflow._fallback_review_plan(
		{
			"title": "医院信息化建设项目",
			"project_number": "XM-001",
			"deadline": "2026-08-20T09:00:00",
			"purchaser": "示例医院",
			"qualification_requirements": "具有独立承担民事责任的能力",
			"source_url": "https://www.ccgp.gov.cn/tender/1",
		}
	)
	assert "XM-001" in plan
	assert "2026-08-20T09:00:00" in plan
	assert "示例医院" in plan
	assert "https://www.ccgp.gov.cn/tender/1" in plan


def test_review_completes_when_deepseek_and_qwen_both_timeout(tmp_path: Path):
	settings = Settings(
		api_token="token",
		data_dir=tmp_path,
		qwen_base_url="http://qwen/v1",
		qwen_api_key="key",
		qwen_model="qwen",
		hermes_url="http://hermes",
		hermes_api_key="key",
		searxng_url="http://search",
		deepseek_url="http://deepseek",
		deepseek_token="key",
		max_concurrent_runs=1,
	)
	store = RunStore(tmp_path / "review.sqlite3")
	run = store.create(
		{"client_run_id": "RUN-REVIEW", "task_id": "TASK-REVIEW", "user_id": "u", "request": "找线索"}
	)
	workflow = LeadWorkflow(settings, store)

	class FailingDeepSeek:
		def review(self, prompt, **kwargs):
			raise TimeoutError("deepseek timeout")

	class FailingQwen:
		def json(self, system, user, default, **kwargs):
			raise TimeoutError("qwen timeout")

	workflow.deepseek = FailingDeepSeek()
	workflow.qwen = FailingQwen()
	result = workflow.review(
		{
			"run_id": run["run_id"],
			"criteria": {"score_threshold": 70},
			"candidates": [
				{
					"title": "医院信息化建设项目",
					"source_url": "https://www.ccgp.gov.cn/tender/1",
					"fingerprint": "abc",
					"relevance_score": 90,
				}
			],
			"partial": False,
		},
		ReviewCandidatesArguments(maximum_candidates=20),
	)
	assert result["partial"] is True
	assert result["candidates"][0]["deepseek_plan"]
	workflow.close()


def test_workflow_produces_traceable_candidate(tmp_path: Path):
	settings = Settings(
		api_token="token",
		data_dir=tmp_path,
		qwen_base_url="http://qwen/v1",
		qwen_api_key="key",
		qwen_model="qwen",
		hermes_url="http://hermes",
		hermes_api_key="key",
		searxng_url="http://search",
		deepseek_url="http://deepseek",
		deepseek_token="key",
		max_concurrent_runs=1,
	)
	store = RunStore(tmp_path / "workflow.sqlite3")
	run = store.create({"client_run_id": "RUN-2", "task_id": "TASK-2", "user_id": "u", "request": "找医疗行业线索"})
	workflow = LeadWorkflow(settings, store)

	class FakeQwen:
		def json(self, system, user, default, **kwargs):
			if "required_schema" in user:
				return {"industry": "医疗", "maximum_results": 10, "score_threshold": 70}
			return [
				{
					"title": "医院信息化建设项目",
					"source_url": "https://example.test/tender/1",
					"purchaser": "示例医院",
					"relevance_score": 92,
					"confidence": 90,
					"risk_level": "低",
				}
			]

	class FakeHermes:
		def research(self, prompt):
			return [{"title": "医院信息化建设项目", "source_url": "https://example.test/tender/1"}]

	class FakeSearxng:
		def search(self, queries, *, limit):
			return [{"title": "医院信息化建设项目", "source_url": "https://example.test/tender/1"}]

	class FakeExtractor:
		def enrich(self, results, *, limit):
			return results

	class FakeDeepSeek:
		def review(self, prompt, **kwargs):
			return []

	class FakeRouter:
		def decide(self, state, *, tools, required_tool):
			if required_tool:
				return AgentDecision(
					type="tool_call",
					tool_call=AgentToolCall(id=f"call-{required_tool}", name=required_tool, arguments={}),
				)
			return AgentDecision(type="answer", content=state.get("summary") or "完成")

	workflow.qwen = FakeQwen()
	workflow.hermes = FakeHermes()
	workflow.searxng = FakeSearxng()
	workflow.extractor = FakeExtractor()
	workflow.deepseek = FakeDeepSeek()
	workflow.router = FakeRouter()
	state = workflow.run(run["run_id"], run["payload"])
	assert state["criteria"]["industry"] == "医疗"
	assert state["candidates"][0]["fingerprint"]
	assert "1 条" in state["summary"]
	trace = store.trace(run["run_id"])
	assert [row["tool_name"] for row in trace["tools"]] == list(workflow.TOOL_SEQUENCE)
	assert all(row["status"] == "completed" for row in trace["tools"])
	workflow.close()
