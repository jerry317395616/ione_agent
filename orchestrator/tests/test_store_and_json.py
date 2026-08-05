from pathlib import Path

from app.clients import DeepSeekClient, parse_json
from app.settings import Settings
from app.store import RunStore
from app.workflow import LeadWorkflow


def test_parse_json_accepts_fenced_payload():
	assert parse_json('result\n```json\n{"intent":"lead_discovery"}\n```', {}) == {"intent": "lead_discovery"}


def test_deepseek_extract_accepts_web_relay_reply():
	assert DeepSeekClient._extract({"status": "completed", "reply": "[{\"fingerprint\": \"abc\"}]"}) == '[{"fingerprint": "abc"}]'


def test_store_is_idempotent_and_persists_result(tmp_path: Path):
	store = RunStore(tmp_path / "runs.sqlite3")
	payload = {"client_run_id": "RUN-1", "task_id": "TASK-1", "user_id": "u", "request": "找线索"}
	first = store.create(payload)
	second = store.create(payload)
	assert first["run_id"] == second["run_id"]
	store.update(first["run_id"], status="completed", result={"candidates": [{"title": "A"}]})
	assert store.get(first["run_id"])["result"]["candidates"][0]["title"] == "A"


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
		def json(self, system, user, default):
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
		def review(self, prompt):
			return []

	workflow.qwen = FakeQwen()
	workflow.hermes = FakeHermes()
	workflow.searxng = FakeSearxng()
	workflow.extractor = FakeExtractor()
	workflow.deepseek = FakeDeepSeek()
	state = workflow.run(run["run_id"], run["payload"])
	assert state["criteria"]["industry"] == "医疗"
	assert state["candidates"][0]["fingerprint"]
	assert "1 条" in state["summary"]
