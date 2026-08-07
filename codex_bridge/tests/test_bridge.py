from __future__ import annotations

import asyncio
import json

from app.bridge import ChatCompletionRequest, CodexBridge, latest_user_text, stream_chunk
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
	assert '"frappe_list_attachments"' in config
	assert '"frappe_attach_word_file"' in config
	assert '"frappe_convert_lead_to_deal"' in config


def test_stream_chunk_is_openai_compatible() -> None:
	chunk = stream_chunk("chatcmpl-test", "ione-agent", {"content": "hello"})
	payload = json.loads(chunk.removeprefix("data: ").strip())
	assert payload["choices"][0]["delta"]["content"] == "hello"


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
	assert any("执行失败：app server unavailable" in chunk for chunk in chunks)
	assert chunks[-1] == "data: [DONE]\n\n"
