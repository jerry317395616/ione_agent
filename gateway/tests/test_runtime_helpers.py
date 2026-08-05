from __future__ import annotations

import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_ROOT))

from app.runtime import WINDOWS_EXECUTION_GUIDANCE, build_prompt, extract_answer  # noqa: E402
from app.settings import Settings  # noqa: E402


def test_build_prompt_keeps_recent_context():
	prompt = build_prompt(
		"继续处理",
		[
			{"role": "user", "content": "检查库存"},
			{"role": "assistant", "content": "发现两项短缺"},
		],
	)
	assert "检查库存" in prompt
	assert "发现两项短缺" in prompt
	assert prompt.endswith("继续处理")


def test_build_prompt_adds_windows_execution_guidance_without_history():
	prompt = build_prompt("Create a spreadsheet", [])
	assert WINDOWS_EXECUTION_GUIDANCE in prompt
	assert prompt.endswith("Create a spreadsheet")


def test_extract_answer_prefers_named_final_answer():
	result = {"session_results": {"debug": "short", "final_answer": "这是最终业务结论"}}
	assert extract_answer(result, []) == "这是最终业务结论"


def test_extract_answer_can_use_agent_event():
	events = [
		{
			"event_type": "agent_output",
			"output_data": {"response": "来自 UFO3 事件的结果"},
		}
	]
	assert extract_answer({}, events) == "来自 UFO3 事件的结果"


def test_openai_base_url_does_not_duplicate_chat_completions(tmp_path):
	settings = Settings(
		gateway_token="secret",
		device_server_api_key="device-secret",
		device_public_ws_url="wss://agent-device.example.com/device/ws",
		device_server_host="127.0.0.1",
		device_server_port=5000,
		qwen_api_base="http://qwen:1234/v1/chat/completions",
		qwen_api_key="key",
		qwen_model="qwen",
		ufo_root=tmp_path,
		data_dir=tmp_path,
		max_rounds=10,
		max_step=15,
		devices={"devices": []},
	)

	assert settings.openai_base_url == "http://qwen:1234/v1"
	assert settings.device_model_api_base == "https://agent-device.example.com/device/openai/v1"
