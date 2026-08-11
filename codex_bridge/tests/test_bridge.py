from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from app.app_server import CodexAppServer
from app.bridge import ChatCompletionRequest, CodexBridge, ProcessDisplay, latest_user_text, stream_chunk
from app.dynamic_tools import DynamicToolProxy
from app.identity import issue_actor_token, with_trusted_identity_context
from app.public_output import sanitize_public_text
from app.settings import Settings
from app.store import ConversationStore


def test_latest_user_text_handles_text_parts() -> None:
	request = ChatCompletionRequest(
		messages=[
			{"role": "assistant", "content": "old"},
			{
				"role": "user",
				"content": [
					{"type": "text", "text": "hello"},
					{"type": "input_text", "text": "world"},
				],
			},
		]
	)
	assert latest_user_text(request) == "hello\nworld"


def test_conversation_store_round_trip(tmp_path) -> None:
	store = ConversationStore(tmp_path / "conversations.sqlite3")
	assert store.get("user", "conversation") is None
	store.set("user", "conversation", "thread-1")
	assert store.get("user", "conversation") == "thread-1"
	store.set("user", "conversation", "thread-2")
	assert store.get("user", "conversation") == "thread-2"
	assert store.count() == 1
	store.delete("user", "conversation")
	assert store.count() == 0
	store.close()


def test_model_catalog_has_selected_model(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	settings = Settings.from_environment()
	catalog = settings.model_catalog()
	models = {model["slug"]: model for model in catalog["models"]}
	assert settings.model in models
	assert models[settings.model]["context_window"] == 1000000
	assert models[settings.model]["include_apps_usage_instructions"] is False
	assert models[settings.model]["include_skills_usage_instructions"] is True
	assert all("DeepSeek" not in model["display_name"] for model in models.values())
	assert all("DeepSeek" not in model["description"] for model in models.values())
	assert models[settings.model]["base_instructions"] == settings.developer_instructions
	assert settings.app_server_message_limit_bytes > 64 * 1024


def test_site_workspace_scope_shares_one_directory(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	workspace_root = tmp_path / "workspaces"
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(workspace_root))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_SCOPE", "site")

	settings = Settings.from_environment()
	bridge = CodexBridge(settings, SimpleNamespace())
	try:
		assert bridge.workspace_for("first@example.com") == workspace_root.resolve()
		assert bridge.workspace_for("second@example.com") == workspace_root.resolve()
	finally:
		bridge.close()


def test_user_workspace_scope_remains_isolated(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_SCOPE", "user")

	settings = Settings.from_environment()
	bridge = CodexBridge(settings, SimpleNamespace())
	try:
		assert bridge.workspace_for("first@example.com") != bridge.workspace_for(
			"second@example.com"
		)
	finally:
		bridge.close()


def test_model_catalog_supports_configured_local_model(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "local-model-key")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	monkeypatch.setenv("IONE_CODEX_MODEL", "qwen3.6-35b-a3b-fp8")
	monkeypatch.setenv("IONE_CODEX_MODEL_PROVIDER", "qwen-local")
	monkeypatch.setenv("IONE_CODEX_MODEL_CONTEXT_WINDOW", "262144")

	settings = Settings.from_environment()
	models = {model["slug"]: model for model in settings.model_catalog()["models"]}
	assert settings.model in models
	assert models[settings.model]["display_name"] == "I-ONE AI Local"
	assert models[settings.model]["context_window"] == 262144
	assert models[settings.model]["auto_compact_token_limit"] == 235929


def test_prepare_writes_mcp_config_without_secret(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	monkeypatch.setenv("IONE_FRAPPE_MCP_URL", "https://manager.example/api/mcp")
	monkeypatch.setenv("IONE_FRAPPE_AUTH_HEADER", "token key:secret")
	settings = Settings.from_environment()
	settings.prepare()
	config = (settings.codex_home / "config.toml").read_text(encoding="utf-8")
	assert "[mcp_servers.manager]" in config
	assert 'env_http_headers = { Authorization = "IONE_FRAPPE_AUTH_HEADER" }' in config
	assert "token key:secret" not in config
	assert settings.process_environment()["IONE_FRAPPE_AUTH_HEADER"] == "token key:secret"
	assert (settings.codex_home / "skills" / "crm-sales" / "SKILL.md").is_file()
	assert (settings.codex_home / "skills" / "lead-proposal-to-deal" / "SKILL.md").is_file()
	assert (settings.codex_home / "skills" / "deal-proposal-to-slides" / "SKILL.md").is_file()
	assert (settings.codex_home / "skills" / "deal-materials-to-promo-video" / "SKILL.md").is_file()
	assert '"frappe_list_attachments"' in config
	assert '"frappe_get_site_catalog"' in config
	assert '"frappe_attach_word_file"' in config
	assert '"frappe_create_crm_lead_package"' in config
	assert '"frappe_convert_lead_to_deal"' in config
	assert '"frappe_read_word_attachment"' in config
	assert '"frappe_upsert_deal_presentation"' in config


def test_prepare_can_limit_skills_and_mcp_tools(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	monkeypatch.setenv("IONE_FRAPPE_MCP_URL", "https://child.example/api/mcp")
	monkeypatch.setenv("IONE_FRAPPE_AUTH_HEADER", "token key:secret")
	monkeypatch.setenv("IONE_CODEX_SKILLS", "tongjianyun")
	monkeypatch.setenv(
		"IONE_FRAPPE_MCP_ENABLED_TOOLS",
		"frappe_get_doctype_meta,frappe_list_documents,frappe_get_document",
	)
	settings = Settings.from_environment()
	settings.prepare()
	config = (settings.codex_home / "config.toml").read_text(encoding="utf-8")
	installed_skills = {path.name for path in (settings.codex_home / "skills").iterdir()}

	assert installed_skills == {"tongjianyun"}
	assert '"frappe_get_doctype_meta"' in config
	assert '"frappe_list_documents"' in config
	assert '"frappe_get_document"' in config
	assert '"frappe_search_doctypes"' not in config
	assert '"frappe_create_crm_lead_package"' not in config


def test_prepare_can_use_app_server_dynamic_tools(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	monkeypatch.setenv("IONE_FRAPPE_MCP_URL", "http://127.0.0.1:17080/api/mcp")
	monkeypatch.setenv("IONE_FRAPPE_AUTH_HEADER", "token key:secret")
	monkeypatch.setenv("IONE_FRAPPE_DYNAMIC_TOOLS", "1")
	settings = Settings.from_environment()
	settings.prepare()
	config = (settings.codex_home / "config.toml").read_text(encoding="utf-8")

	assert settings.frappe_dynamic_tools is True
	assert "[mcp_servers.manager]" not in config


def test_dynamic_tool_proxy_filters_and_calls_enabled_tools(monkeypatch) -> None:
	settings = SimpleNamespace(
		frappe_mcp_enabled_tools=("frappe_get_context",),
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_auth_header="token key:secret",
		frappe_site_host="child.example",
	)
	proxy = DynamicToolProxy(settings)

	def fake_rpc(method, params):
		if method == "tools/list":
			return {
				"tools": [
					{
						"name": "frappe_get_context",
						"description": "Get context",
						"inputSchema": {"type": "object", "properties": {}},
					},
					{"name": "frappe_create_document", "inputSchema": {"type": "object"}},
				]
			}
		assert params == {"name": "frappe_get_context", "arguments": {"actor_token": "token"}}
		return {
			"content": [{"type": "text", "text": '{"site":"child.example"}'}],
			"isError": False,
		}

	monkeypatch.setattr(proxy, "_rpc", fake_rpc)

	async def probe():
		specs = await proxy.specs()
		assert [spec["name"] for spec in specs] == ["frappe_get_context"]
		result = await proxy.call("frappe_get_context", {"actor_token": "token"})
		assert result["success"] is True
		assert result["contentItems"][0]["text"] == '{"site":"child.example"}'
		denied = await proxy.call("frappe_create_document", {})
		assert denied["success"] is False

	asyncio.run(probe())


def test_stream_chunk_is_openai_compatible() -> None:
	chunk = stream_chunk("chatcmpl-test", "ione-agent", {"content": "hello"})
	payload = json.loads(chunk.removeprefix("data: ").strip())
	assert payload["choices"][0]["delta"]["content"] == "hello"


def test_trusted_identity_context_uses_manager_email() -> None:
	secret = "identity-secret-longer-than-thirty-two-characters"
	token = issue_actor_token(
		email="owner@example.com",
		user_hint="Administrator",
		audience="manager.myyr.top",
		secret=secret,
		now=1_800_000_000,
	)
	payload_segment = token.split(".")[1]
	payload = json.loads(base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4)))
	assert payload["email"] == "owner@example.com"
	assert payload["user"] == "Administrator"
	assert payload["aud"] == "manager.myyr.top"
	assert payload["exp"] - payload["iat"] == 600

	text = with_trusted_identity_context(
		"创建一个医疗行业线索",
		email="owner@example.com",
		user_hint="Administrator",
		mcp_url="https://manager.myyr.top/api/method/ione_core.mcp.server.handle_mcp",
		secret=secret,
	)
	assert text.endswith("创建一个医疗行业线索")
	assert "<ione_trusted_session>" in text
	assert "actor_token=ione1." in text
	assert "Pass actor_token to every configured Frappe tool" in text
	assert "do not retry without it" in text
	assert "owner@example.com" not in text


def test_trusted_identity_context_ignores_invalid_email() -> None:
	assert (
		with_trusted_identity_context(
			"hello",
			email="not-an-email",
			mcp_url="https://manager.myyr.top/api/mcp",
			secret="identity-secret-longer-than-thirty-two-characters",
		)
		== "hello"
	)


def test_trusted_identity_context_supports_internal_mcp_route() -> None:
	secret = "identity-secret-longer-than-thirty-two-characters"
	text = with_trusted_identity_context(
		"hello",
		email="owner@example.com",
		user_hint="Administrator",
		mcp_url="http://127.0.0.1:17080/api/mcp",
		secret=secret,
		audience="child.example",
	)
	token = text.split("actor_token=", 1)[1].splitlines()[0]
	payload_segment = token.split(".")[1]
	payload = json.loads(base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4)))
	assert payload["aud"] == "child.example"


def test_prepare_can_route_mcp_internally_with_site_host(monkeypatch, tmp_path) -> None:
	bin_path = tmp_path / "codex"
	bin_path.write_text("", encoding="utf-8")
	monkeypatch.setenv("IONE_CODEX_BRIDGE_TOKEN", "bridge")
	monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek")
	monkeypatch.setenv("IONE_CODEX_BIN", str(bin_path))
	monkeypatch.setenv("IONE_CODEX_HOME", str(tmp_path / "codex-home"))
	monkeypatch.setenv("IONE_CODEX_DATA_DIR", str(tmp_path / "data"))
	monkeypatch.setenv("IONE_CODEX_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
	monkeypatch.setenv("IONE_FRAPPE_MCP_URL", "http://127.0.0.1:17080/api/mcp")
	monkeypatch.setenv("IONE_FRAPPE_AUTH_HEADER", "token key:secret")
	monkeypatch.setenv("IONE_FRAPPE_SITE_HOST", "child.example")
	monkeypatch.setenv("IONE_MANAGER_IDENTITY_AUDIENCE", "child.example")
	settings = Settings.from_environment()
	settings.prepare()
	config = (settings.codex_home / "config.toml").read_text(encoding="utf-8")
	assert settings.identity_audience == "child.example"
	assert 'url = "http://127.0.0.1:17080/api/mcp"' in config
	assert 'Host = "IONE_FRAPPE_SITE_HOST"' in config
	assert settings.process_environment()["IONE_FRAPPE_SITE_HOST"] == "child.example"


def test_app_server_health_requires_live_output_reader() -> None:
	async def probe() -> None:
		server = CodexAppServer(SimpleNamespace())
		server.process = SimpleNamespace(returncode=None)
		assert server.alive is False

		reader_task = asyncio.create_task(asyncio.sleep(10))
		server.reader_task = reader_task
		assert server.alive is True

		reader_task.cancel()
		await asyncio.gather(reader_task, return_exceptions=True)
		assert server.alive is False

	asyncio.run(probe())


class FailingBridge:
	async def _events(self, *args, **kwargs):
		raise RuntimeError("app server unavailable")
		yield


def test_stream_reports_app_server_failure() -> None:
	async def collect() -> list[str]:
		bridge = object.__new__(FailingBridge)
		bridge.stream = CodexBridge.stream.__get__(bridge, FailingBridge)
		request = ChatCompletionRequest(
			messages=[{"role": "user", "content": "你好"}], stream=True
		)
		return [
			chunk
			async for chunk in bridge.stream(
				request, user_id="user", conversation_id="conversation"
			)
		]

	chunks = asyncio.run(collect())
	assert any("I-ONE Agent 暂时无法完成本次请求" in chunk for chunk in chunks)
	assert all("app server unavailable" not in chunk for chunk in chunks)
	assert chunks[-1] == "data: [DONE]\n\n"


def test_public_output_hides_runtime_and_provider_details() -> None:
	text = sanitize_public_text(
		"Codex App Server called DeepSeek V4 Flash at https://api.deepseek.com/v1 "
		"using DEEPSEEK_API_KEY from /opt/ione-codex-agent/current. "
		"Fallback app server received sk-1234567890abcdef."
	)
	assert "Codex" not in text
	assert "DeepSeek" not in text
	assert "api.deepseek.com" not in text
	assert "DEEPSEEK_API_KEY" not in text
	assert "/opt/ione-codex-agent" not in text
	assert "app server" not in text.lower()
	assert "sk-1234567890abcdef" not in text
	assert "I-ONE" in text
	assert "ione1." not in sanitize_public_text(
		"credential ione1.eyJlbWFpbCI6Im93bmVyQGV4YW1wbGUuY29tIn0.ABCDEFGHIJKLMNOP"
	)


class SensitiveAnswerBridge:
	async def _events(self, *args, **kwargs):
		yield {"method": "item/agentMessage/delta", "params": {"delta": "Codex App "}}
		yield {
			"method": "item/agentMessage/delta",
			"params": {"delta": "Server 使用 DeepSeek V4 Flash 完成。"},
		}
		yield {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}


def test_stream_sanitizes_sensitive_answer_across_deltas() -> None:
	async def collect() -> str:
		bridge = object.__new__(SensitiveAnswerBridge)
		bridge.stream = CodexBridge.stream.__get__(bridge, SensitiveAnswerBridge)
		request = ChatCompletionRequest(messages=[{"role": "user", "content": "介绍你自己"}], stream=True)
		return "".join(
			[
				chunk
				async for chunk in bridge.stream(
					request, user_id="user", conversation_id="conversation"
				)
			]
		)

	chunks = asyncio.run(collect())
	assert "Codex" not in chunks
	assert "DeepSeek" not in chunks
	assert "I-ONE" in chunks


class ProcessDisplayBridge:
	async def _events(self, *args, **kwargs):
		yield {
			"method": "item/reasoning/summaryTextDelta",
			"params": {
				"itemId": "reasoning-1",
				"delta": "先确认业务对象，再查询数据。token=top-secret ",
			},
		}
		yield {
			"method": "item/reasoning/summaryTextDelta",
			"params": {"itemId": "reasoning-1", "delta": "路径 /home/zyd/private.txt"},
		}
		yield {
			"method": "item/completed",
			"params": {"item": {"id": "reasoning-1", "type": "reasoning"}},
		}
		yield {
			"method": "bridge/dynamicTool/started",
			"params": {
				"callId": "call-1",
				"tool": "frappe_list_documents",
				"arguments": {"password": "must-not-leak"},
			},
		}
		yield {
			"method": "bridge/dynamicTool/completed",
			"params": {
				"callId": "call-1",
				"tool": "frappe_list_documents",
				"success": True,
				"durationMs": 1250,
				"result": {"secret": "must-not-leak"},
			},
		}
		yield {
			"method": "item/started",
			"params": {
				"item": {
					"id": "command-1",
					"type": "commandExecution",
					"command": "curl -H 'Authorization: Bearer must-not-leak'",
				}
			},
		}
		yield {
			"method": "item/completed",
			"params": {
				"item": {
					"id": "command-1",
					"type": "commandExecution",
					"status": "completed",
					"durationMs": 250,
					"aggregatedOutput": "must-not-leak",
				}
			},
		}
		yield {"method": "item/agentMessage/delta", "params": {"delta": "处理完成。"}}
		yield {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}


def test_stream_exposes_safe_dynamic_process_and_summary() -> None:
	async def collect() -> list[str]:
		bridge = object.__new__(ProcessDisplayBridge)
		bridge.stream = CodexBridge.stream.__get__(bridge, ProcessDisplayBridge)
		request = ChatCompletionRequest(messages=[{"role": "user", "content": "查询数据"}], stream=True)
		return [
			chunk
			async for chunk in bridge.stream(
				request, user_id="user", conversation_id="conversation"
			)
		]

	chunks = asyncio.run(collect())
	deltas = []
	for chunk in chunks:
		if not chunk.startswith("data: {"):
			continue
		payload = json.loads(chunk.removeprefix("data: ").strip())
		deltas.append(payload["choices"][0]["delta"])
	reasoning = "".join(delta.get("reasoning_content", "") for delta in deltas)
	content = "".join(delta.get("content", "") for delta in deltas)

	assert "处理过程" in reasoning
	assert "思考摘要" in reasoning
	assert "先确认业务对象，再查询数据" in reasoning
	assert "查询业务数据" in reasoning
	assert "1.2 秒" in reasoning
	assert "执行受控系统操作" in reasoning
	assert "处理完成，正在整理结果" in reasoning
	assert "处理完成。" in content
	assert "must-not-leak" not in reasoning
	assert "top-secret" not in reasoning
	assert "/home/zyd" not in reasoning


def test_dynamic_tool_request_publishes_safe_lifecycle_events() -> None:
	class FakeProxy:
		async def call(self, tool, arguments):
			assert tool == "frappe_get_document"
			assert arguments == {"name": "secret-record"}
			return {"success": True, "contentItems": [{"text": "private-result"}]}

	async def probe() -> tuple[list[dict], list[dict]]:
		server = object.__new__(CodexAppServer)
		server.dynamic_tool_proxy = FakeProxy()
		published: list[dict] = []
		written: list[dict] = []

		async def publish(payload):
			published.append(payload)

		async def write(payload):
			written.append(payload)

		server._publish = publish
		server._write = write
		await server._handle_server_request(
			{
				"id": 7,
				"method": "item/tool/call",
				"params": {
					"threadId": "thread-1",
					"turnId": "turn-1",
					"callId": "call-1",
					"tool": "frappe_get_document",
					"arguments": {"name": "secret-record"},
				},
			}
		)
		return published, written

	published, written = asyncio.run(probe())
	assert [event["method"] for event in published] == [
		"bridge/dynamicTool/started",
		"bridge/dynamicTool/completed",
	]
	serialized_events = json.dumps(published, ensure_ascii=False)
	assert "frappe_get_document" in serialized_events
	assert "secret-record" not in serialized_events
	assert "private-result" not in serialized_events
	assert written == [
		{
			"id": 7,
			"result": {"success": True, "contentItems": [{"text": "private-result"}]},
		}
	]


def test_process_display_builds_fallback_summary_from_actual_activities() -> None:
	display = ProcessDisplay()
	started = display.consume(
		{
			"method": "bridge/dynamicTool/started",
			"params": {"tool": "frappe_list_documents"},
		}
	)
	completed = display.consume(
		{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}
	)
	text = "".join([*started, *completed])

	assert "正在查询业务数据" in text
	assert "思考摘要" in text
	assert "执行了查询业务数据" in text
