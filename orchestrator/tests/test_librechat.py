from __future__ import annotations

import asyncio
from pathlib import Path

from app.librechat import ChatCompletionRequest, ChatMessage, ConversationStore, LibreChatBridge, message_text
from app.settings import Settings


def make_settings(tmp_path: Path) -> Settings:
	return Settings(
		api_token="orchestrator-token",
		data_dir=tmp_path,
		qwen_base_url="http://qwen/v1",
		qwen_api_key="qwen-key",
		qwen_model="qwen",
		hermes_url="http://hermes",
		hermes_api_key="hermes-key",
		searxng_url="http://search",
		deepseek_url="http://deepseek",
		deepseek_token="deepseek-key",
		max_concurrent_runs=1,
		librechat_api_token="librechat-token",
		frappe_base_url="https://frappe.example",
		frappe_api_key="frappe-key",
		frappe_api_secret="frappe-secret",
	)


def test_librechat_configuration_requires_bridge_and_frappe_credentials(tmp_path: Path):
	configured = make_settings(tmp_path)
	assert configured.librechat_ready is True
	assert Settings(
		api_token="token",
		data_dir=tmp_path,
		qwen_base_url="http://qwen/v1",
		qwen_api_key="key",
		qwen_model="qwen",
		hermes_url="",
		hermes_api_key="",
		searxng_url="",
		deepseek_url="",
		deepseek_token="",
		max_concurrent_runs=1,
	).librechat_ready is False


def test_message_text_supports_openai_multimodal_content():
	message = ChatMessage(
		role="user",
		content=[
			{"type": "text", "text": "寻找医疗行业线索"},
			{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
			{"type": "input_text", "text": "仅限近七天"},
		],
	)
	assert message_text(message) == "寻找医疗行业线索\n仅限近七天"


def test_conversation_store_keeps_frappe_session_mapping(tmp_path: Path):
	store = ConversationStore(tmp_path / "librechat.sqlite3")
	assert store.get("user-1", "conversation-1") is None
	store.set("user-1", "conversation-1", "IONE-AGENT-SESSION-0001")
	assert store.get("user-1", "conversation-1") == "IONE-AGENT-SESSION-0001"


def test_bridge_returns_frappe_answer_and_reuses_session(tmp_path: Path):
	class FakeFrappe:
		def __init__(self):
			self.sessions = []

		async def send_message(self, message, session):
			self.sessions.append(session)
			return {
				"session": session or "IONE-AGENT-SESSION-0001",
				"run": {"name": "IONE-AGENT-RUN-0001"},
			}

		async def get_run(self, run_id):
			return {"status": "Completed", "response_text": "已找到一条可核验线索。"}

		async def stop_run(self, run_id):
			return None

		async def close(self):
			return None

	async def scenario():
		bridge = LibreChatBridge(make_settings(tmp_path))
		await bridge.frappe.close()
		bridge.frappe = FakeFrappe()
		request = ChatCompletionRequest(
			model="ione-agent",
			messages=[{"role": "user", "content": "寻找医疗行业线索"}],
		)
		first = await bridge.complete(request, user_id="user-1", conversation_id="conversation-1")
		second = await bridge.complete(request, user_id="user-1", conversation_id="conversation-1")
		assert first["choices"][0]["message"]["content"] == "已找到一条可核验线索。"
		assert second["object"] == "chat.completion"
		assert bridge.frappe.sessions == [None, "IONE-AGENT-SESSION-0001"]
		await bridge.close()

	asyncio.run(scenario())
