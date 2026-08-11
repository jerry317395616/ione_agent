from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from app.app_server import CodexAppServer
from app.bridge import ChatCompletionRequest, CodexBridge, latest_user_text, stream_chunk
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
