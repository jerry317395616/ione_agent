from __future__ import annotations

import asyncio
import base64
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.app_server import CodexAppServer
from app.bridge import ChatCompletionRequest, CodexBridge, ProcessDisplay, latest_user_text, stream_chunk
from app.dynamic_tools import DynamicToolProxy
from app.identity import ToolIdentity, issue_actor_token, tool_identity, with_trusted_identity_context
from app.oracle_browser import OracleBrowserResult, parse_oracle_action
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


def test_parse_oracle_action_accepts_fenced_json() -> None:
	action = parse_oracle_action(
		'```json\n{"action":"tool","tool":"frappe_get_context","arguments":{}}\n```'
	)
	assert action == {
		"action": "tool",
		"tool": "frappe_get_context",
		"arguments": {},
	}


def test_parse_oracle_action_treats_plain_text_as_reply() -> None:
	assert parse_oracle_action("你好") == {"action": "reply", "content": "你好"}


def test_oracle_browser_drives_permission_aware_tool_then_replies(tmp_path) -> None:
	class FakeProxy:
		async def specs(self):
			return [
				{
					"name": "frappe_get_context",
					"description": "读取上下文",
					"inputSchema": {"type": "object", "properties": {}},
				}
			]

		async def call(self, tool, arguments, *, identity):
			assert tool == "frappe_get_context"
			assert arguments == {}
			assert identity == ToolIdentity("owner@example.com", "Administrator", "child.example")
			return {
				"contentItems": [{"type": "inputText", "text": '{"site":"child.example"}'}],
				"success": True,
			}

	class FakeOracle:
		def __init__(self):
			self.prompts = []

		async def ask(self, *, prompt, conversation_key):
			self.prompts.append(prompt)
			assert conversation_key.startswith("child-")
			if len(self.prompts) == 1:
				return OracleBrowserResult(
					'{"action":"tool","tool":"frappe_get_context","arguments":{}}'
				)
			return OracleBrowserResult('{"action":"reply","content":"当前站点为 child.example。"}')

	settings = SimpleNamespace(
		workspace_scope="site",
		workspace_root=tmp_path,
		data_dir=tmp_path,
		oracle_browser_enabled=False,
		oracle_browser_max_tool_rounds=3,
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_site_host="child.example",
		identity_audience="child.example",
	)
	server = SimpleNamespace(dynamic_tool_proxy=FakeProxy())
	bridge = CodexBridge(settings, server)
	bridge.oracle_browser = FakeOracle()
	request = ChatCompletionRequest(messages=[{"role": "user", "content": "这是哪个站点？"}])
	try:
		answer = asyncio.run(
			bridge._oracle_answer(
				request,
				user_id="user-1",
				conversation_id="conversation-1",
				manager_user_email="owner@example.com",
				manager_user_hint="Administrator",
			)
		)
		assert answer == "当前站点为 child.example。"
		assert len(bridge.oracle_browser.prompts) == 2
	finally:
		bridge.close()


def test_oracle_browser_can_answer_without_site_identity(tmp_path) -> None:
	class FakeProxy:
		async def specs(self):
			return []

	class FakeOracle:
		async def ask(self, *, prompt, conversation_key):
			return OracleBrowserResult('{"action":"reply","content":"你好。"}')

	settings = SimpleNamespace(
		workspace_scope="site",
		workspace_root=tmp_path,
		data_dir=tmp_path,
		oracle_browser_enabled=False,
		oracle_browser_max_tool_rounds=3,
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_site_host="child.example",
		identity_audience="child.example",
	)
	bridge = CodexBridge(settings, SimpleNamespace(dynamic_tool_proxy=FakeProxy()))
	bridge.oracle_browser = FakeOracle()
	try:
		answer = asyncio.run(
			bridge._oracle_answer(
				ChatCompletionRequest(messages=[{"role": "user", "content": "你好"}]),
				user_id="anonymous",
				conversation_id="conversation-1",
				manager_user_email=None,
				manager_user_hint=None,
			)
		)
		assert answer == "你好。"
	finally:
		bridge.close()


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
		assert bridge.workspace_for("first@example.com") != bridge.workspace_for("second@example.com")
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
	assert (
		settings.codex_home
		/ "skills"
		/ "analyze-tongjianyun-recipe"
		/ "SKILL.md"
	).is_file()
	assert '"frappe_list_attachments"' in config
	assert '"frappe_get_site_catalog"' in config
	assert '"frappe_attach_word_file"' in config
	assert '"frappe_create_crm_lead_package"' in config
	assert '"frappe_convert_lead_to_deal"' in config
	assert '"frappe_read_word_attachment"' in config
	assert '"frappe_upsert_deal_presentation"' in config
	assert '"frappe_upsert_tongjianyun_recipe"' in config
	assert "frappe_upsert_tongjianyun_recipe" in (
		settings.bundled_skills_dir / "tongjianyun" / "SKILL.md"
	).read_text(encoding="utf-8")


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
		identity_shared_secret="identity-secret-longer-than-thirty-two-characters",
	)
	proxy = DynamicToolProxy(settings)

	def fake_rpc(method, params):
		if method == "tools/list":
			return {
				"tools": [
					{
						"name": "frappe_get_context",
						"description": "Get context",
						"inputSchema": {
							"type": "object",
							"properties": {"actor_token": {"type": "string"}},
						},
					},
					{"name": "frappe_create_document", "inputSchema": {"type": "object"}},
				]
			}
		assert params["name"] == "frappe_get_context"
		assert params["arguments"]["actor_token"].startswith("ione1.")
		assert params["arguments"]["actor_token"] != "stale-model-token"
		return {
			"content": [{"type": "text", "text": '{"site":"child.example"}'}],
			"isError": False,
		}

	monkeypatch.setattr(proxy, "_rpc", fake_rpc)

	async def probe():
		specs = await proxy.specs()
		assert [spec["name"] for spec in specs] == ["frappe_get_context"]
		assert "actor_token" not in specs[0]["inputSchema"]["properties"]
		identity = ToolIdentity("owner@example.com", "Administrator", "child.example")
		result = await proxy.call(
			"frappe_get_context",
			{"actor_token": "stale-model-token"},
			identity=identity,
		)
		assert result["success"] is True
		assert result["contentItems"][0]["text"] == '{"site":"child.example"}'
		denied = await proxy.call("frappe_create_document", {}, identity=identity)
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


def test_tool_identity_uses_site_host_without_issuing_token() -> None:
	identity = tool_identity(
		email="owner@example.com",
		user_hint="Administrator",
		mcp_url="http://127.0.0.1:17080/api/mcp",
		site_host="child.example",
	)
	assert identity == ToolIdentity("owner@example.com", "Administrator", "child.example")
	assert "ione1." not in repr(identity)


def test_dynamic_tool_proxy_rejects_missing_identity(monkeypatch) -> None:
	settings = SimpleNamespace(
		frappe_mcp_enabled_tools=("frappe_get_context",),
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_auth_header="token key:secret",
		frappe_site_host="child.example",
		identity_shared_secret="identity-secret-longer-than-thirty-two-characters",
	)
	proxy = DynamicToolProxy(settings)
	proxy._specs = [{"name": "frappe_get_context"}]
	proxy._tool_names = {"frappe_get_context"}
	monkeypatch.setattr(proxy, "_rpc", lambda *_args: (_ for _ in ()).throw(AssertionError()))

	result = asyncio.run(proxy.call("frappe_get_context", {}, identity=None))
	assert result["success"] is False


def test_dynamic_tool_proxy_issues_token_at_each_call(monkeypatch) -> None:
	settings = SimpleNamespace(
		frappe_mcp_enabled_tools=("frappe_get_context",),
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_auth_header="token key:secret",
		frappe_site_host="child.example",
		identity_shared_secret="identity-secret-longer-than-thirty-two-characters",
	)
	proxy = DynamicToolProxy(settings)
	proxy._specs = [{"name": "frappe_get_context"}]
	proxy._tool_names = {"frappe_get_context"}
	issued_tokens = []

	def fake_rpc(_method, params):
		issued_tokens.append(params["arguments"]["actor_token"])
		return {"content": [], "structuredContent": {}, "isError": False}

	clock = iter((1_800_000_000, 1_800_001_200))
	monkeypatch.setattr(proxy, "_rpc", fake_rpc)
	monkeypatch.setattr("app.identity.time.time", lambda: next(clock))
	identity = ToolIdentity("owner@example.com", "Administrator", "child.example")

	async def probe():
		await proxy.call("frappe_get_context", {}, identity=identity)
		await proxy.call("frappe_get_context", {}, identity=identity)

	asyncio.run(probe())
	payloads = []
	for token in issued_tokens:
		segment = token.split(".")[1]
		payloads.append(json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))))
	assert [payload["iat"] for payload in payloads] == [1_800_000_000, 1_800_001_200]
	assert all(payload["exp"] - payload["iat"] == 600 for payload in payloads)
	assert issued_tokens[0] != issued_tokens[1]


def test_app_server_clears_only_owned_thread_identity() -> None:
	server = object.__new__(CodexAppServer)
	server.active_tool_identities = {}
	first = ToolIdentity("first@example.com", "first", "child.example")
	second = ToolIdentity("second@example.com", "second", "child.example")

	server.bind_tool_identity("thread-1", first)
	server.bind_tool_identity("thread-1", second)
	server.clear_tool_identity("thread-1", first)
	assert server.active_tool_identities["thread-1"] is second
	server.clear_tool_identity("thread-1", second)
	assert "thread-1" not in server.active_tool_identities


def test_dynamic_turn_does_not_put_token_in_model_input(tmp_path) -> None:
	class FakeAppServer:
		dynamic_tool_proxy = object()

		def __init__(self):
			self.identity = None
			self.turn_input = ""

		def bind_tool_identity(self, thread_id, identity):
			assert thread_id == "thread-1"
			self.identity = identity

		def clear_tool_identity(self, thread_id, identity):
			assert self.identity is identity
			self.identity = None

		@asynccontextmanager
		async def subscribe(self, thread_id):
			queue = asyncio.Queue()
			await queue.put(
				{
					"method": "turn/completed",
					"params": {"threadId": thread_id, "turn": {"id": "turn-1"}},
				}
			)
			yield queue

		async def request(self, method, params):
			assert method == "turn/start"
			self.turn_input = params["input"][0]["text"]
			return {"turn": {"id": "turn-1"}}

	settings = SimpleNamespace(
		workspace_scope="site",
		workspace_root=tmp_path,
		frappe_mcp_url="http://127.0.0.1:17080/api/mcp",
		frappe_site_host="child.example",
		identity_shared_secret="identity-secret-longer-than-thirty-two-characters",
		identity_audience="child.example",
		model="ione-agent",
		keepalive_seconds=1,
	)
	server = FakeAppServer()
	bridge = object.__new__(CodexBridge)
	bridge.settings = settings
	bridge.app_server = server
	bridge.locks = defaultdict(asyncio.Lock)

	async def thread(_user_id, _conversation_id):
		return "thread-1"

	bridge._thread = thread
	request = ChatCompletionRequest(messages=[{"role": "user", "content": "保存食谱"}])

	async def collect():
		return [
			event
			async for event in bridge._events(
				request,
				user_id="user-1",
				conversation_id="conversation-1",
				manager_user_email="owner@example.com",
				manager_user_hint="Administrator",
			)
		]

	asyncio.run(collect())
	assert server.turn_input == "保存食谱"
	assert "ione1." not in server.turn_input
	assert "actor_token" not in server.turn_input
	assert server.identity is None


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
		request = ChatCompletionRequest(messages=[{"role": "user", "content": "你好"}], stream=True)
		return [
			chunk async for chunk in bridge.stream(request, user_id="user", conversation_id="conversation")
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


class PhasedAgentMessageBridge:
	async def _events(self, *args, **kwargs):
		yield {
			"method": "item/started",
			"params": {
				"item": {
					"id": "commentary-1",
					"type": "agentMessage",
					"phase": "commentary",
				}
			},
		}
		yield {
			"method": "item/agentMessage/delta",
			"params": {"itemId": "commentary-1", "delta": "先检查食谱记录。"},
		}
		yield {
			"method": "item/completed",
			"params": {
				"item": {
					"id": "commentary-1",
					"type": "agentMessage",
					"phase": "commentary",
					"text": "先检查食谱记录。",
				}
			},
		}
		yield {
			"method": "bridge/dynamicTool/started",
			"params": {"tool": "frappe_get_document"},
		}
		yield {
			"method": "bridge/dynamicTool/completed",
			"params": {"tool": "frappe_get_document", "success": True},
		}
		yield {
			"method": "item/started",
			"params": {
				"item": {
					"id": "answer-1",
					"type": "agentMessage",
					"phase": "final_answer",
				}
			},
		}
		yield {
			"method": "item/agentMessage/delta",
			"params": {"itemId": "answer-1", "delta": "食谱已成功上传。"},
		}
		yield {
			"method": "item/completed",
			"params": {
				"item": {
					"id": "answer-1",
					"type": "agentMessage",
					"phase": "final_answer",
					"text": "食谱已成功上传。",
				}
			},
		}
		yield {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}


def test_stream_keeps_commentary_out_of_final_content() -> None:
	async def collect() -> list[str]:
		bridge = object.__new__(PhasedAgentMessageBridge)
		bridge.stream = CodexBridge.stream.__get__(bridge, PhasedAgentMessageBridge)
		request = ChatCompletionRequest(messages=[{"role": "user", "content": "上传食谱"}], stream=True)
		return [
			chunk async for chunk in bridge.stream(request, user_id="user", conversation_id="conversation")
		]

	deltas = []
	for chunk in asyncio.run(collect()):
		if chunk.startswith("data: {"):
			payload = json.loads(chunk.removeprefix("data: ").strip())
			deltas.append(payload["choices"][0]["delta"])
	content = "".join(delta.get("content", "") for delta in deltas)
	reasoning = "".join(delta.get("reasoning_content", "") for delta in deltas)

	assert content == "食谱已成功上传。"
	assert "先检查食谱记录" not in content
	assert "读取业务记录" in reasoning


def test_non_stream_completion_returns_only_final_answer() -> None:
	async def collect() -> dict:
		bridge = object.__new__(PhasedAgentMessageBridge)
		bridge.complete = CodexBridge.complete.__get__(bridge, PhasedAgentMessageBridge)
		request = ChatCompletionRequest(messages=[{"role": "user", "content": "上传食谱"}])
		return await bridge.complete(request, user_id="user", conversation_id="conversation")

	response = asyncio.run(collect())
	assert response["choices"][0]["message"]["content"] == "食谱已成功上传。"


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
			[chunk async for chunk in bridge.stream(request, user_id="user", conversation_id="conversation")]
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
			chunk async for chunk in bridge.stream(request, user_id="user", conversation_id="conversation")
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
		async def call(self, tool, arguments, *, identity):
			assert tool == "frappe_get_document"
			assert arguments == {"name": "secret-record"}
			assert identity == ToolIdentity("owner@example.com", "Administrator", "child.example")
			return {"success": True, "contentItems": [{"text": "private-result"}]}

	async def probe() -> tuple[list[dict], list[dict]]:
		server = object.__new__(CodexAppServer)
		server.dynamic_tool_proxy = FakeProxy()
		server.active_tool_identities = {
			"thread-1": ToolIdentity("owner@example.com", "Administrator", "child.example")
		}
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
	completed = display.consume({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
	text = "".join([*started, *completed])

	assert "正在查询业务数据" in text
	assert "思考摘要" in text
	assert "执行了查询业务数据" in text
