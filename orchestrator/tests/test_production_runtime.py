from pathlib import Path

from app.contracts import AgentToolCall, LeadAgentState, RiskLevel, ToolSpec
from app.model_router import ModelRouter
from app.policy import ToolPolicy
from app.settings import Settings
from app.store import RunStore
from app.tooling import GovernedToolNode, ToolRegistry
from pydantic import BaseModel, ConfigDict


class EmptyArguments(BaseModel):
	model_config = ConfigDict(extra="forbid")


def _run(store: RunStore) -> dict:
	return store.create(
		{
			"client_run_id": "RUN-PROD-1",
			"task_id": "TASK-PROD-1",
			"user_id": "user@example.com",
			"request": "找医疗行业线索",
		}
	)


def test_high_risk_tool_requires_approval():
	spec = ToolSpec(
		name="send_external_email",
		version="1.0.0",
		description="发送外部邮件",
		argument_model=EmptyArguments,
		risk_level=RiskLevel.HIGH_WRITE,
	)
	decision = ToolPolicy().evaluate(spec, roles=["System Manager"])
	assert not decision.allowed
	assert decision.requires_approval


def test_tool_execution_is_idempotent_and_audited(tmp_path: Path):
	store = RunStore(tmp_path / "runs.sqlite3")
	run = _run(store)
	registry = ToolRegistry()
	calls = []

	def handler(state, arguments):
		calls.append(state["run_id"])
		return {"summary": "完成"}

	registry.register(
		ToolSpec(
			name="read_once",
			version="1.0.0",
			description="幂等读取",
			argument_model=EmptyArguments,
		),
		handler,
	)
	node = GovernedToolNode(registry, ToolPolicy(), store)
	state: LeadAgentState = {
		"run_id": run["run_id"],
		"roles": [],
		"messages": [],
		"completed_tools": [],
		"tool_results": [],
		"pending_tool_call": AgentToolCall(
			id="call-1", name="read_once", arguments={}
		).model_dump(mode="json"),
	}
	first = node(state)
	second = node(state)
	assert first["summary"] == "完成"
	assert second["tool_results"][-1]["idempotent_replay"] is True
	assert calls == [run["run_id"]]
	trace = store.trace(run["run_id"])
	assert len(trace["tools"]) == 1
	assert trace["tools"][0]["attempt_count"] == 1


def test_model_router_normalizes_openai_style_tool_calls():
	payload = ModelRouter._normalize(
		{
			"type": "tool_call",
			"tool_calls": [{"name": "search_public_tenders", "args": {"maximum_results": 10}}],
		}
	)
	assert payload["tool_call"]["id"].startswith("call_")
	assert payload["tool_call"]["arguments"] == {"maximum_results": 10}


def test_model_router_prefers_deepseek_native_tool_calls(tmp_path: Path):
	settings = Settings(
		api_token="token",
		data_dir=tmp_path,
		qwen_base_url="http://qwen/v1",
		qwen_api_key="key",
		qwen_model="qwen",
		hermes_url="http://hermes",
		hermes_api_key="key",
		searxng_url="http://search",
		deepseek_url="https://api.deepseek.test",
		deepseek_token="key",
		max_concurrent_runs=1,
	)

	class FakeDeepSeek:
		def tool_decision(self, system, user, *, tools, **kwargs):
			assert tools[0]["name"] == "search_public_tenders"
			return {
				"type": "tool_call",
				"tool_call": {
					"id": "call-deepseek",
					"name": "search_public_tenders",
					"arguments": {"maximum_results": 20},
				},
			}

	class UnexpectedQwen:
		def json(self, *args, **kwargs):
			raise AssertionError("Qwen should only be used when DeepSeek fails")

	router = ModelRouter(settings, FakeDeepSeek(), UnexpectedQwen())
	decision = router.decide(
		{"run_id": "run-1", "request": "找医疗线索"},
		tools=[
			{
				"name": "search_public_tenders",
				"description": "搜索公开招标",
				"arguments": {"type": "object", "properties": {}},
			}
		],
		eligible_tools=["search_public_tenders"],
	)
	assert decision.tool_call is not None
	assert decision.tool_call.name == "search_public_tenders"
	assert decision.tool_call.arguments == {"maximum_results": 20}
