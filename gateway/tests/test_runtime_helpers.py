from __future__ import annotations

import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
OFFLINE_DOCKERFILE = GATEWAY_ROOT / "Dockerfile.offline"
sys.path.insert(0, str(GATEWAY_ROOT))

from app.runtime import (  # noqa: E402
	DEVICE_KEEPALIVE_INTERVAL_SECONDS,
	UFO_MAX_TOKENS,
	WINDOWS_EXECUTION_GUIDANCE,
	build_prompt,
	device_heartbeat_message,
	extract_answer,
	failure_message,
	heartbeat_client_type,
	normalize_app_response,
	patch_ufo_app_response_source,
	patch_ufo_openai_runtime,
	probe_websocket_server,
	result_failed,
	task_execution_failed,
)
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


def test_legacy_nonvisual_app_response_is_normalized():
	response = normalize_app_response(
		{
			"Observation": "WeChat is already visible",
			"Thought": "No additional action is required",
			"Function": "",
			"Args": {},
			"Status": "FINISH",
			"Plan": [],
			"Comment": "微信已打开。",
		}
	)
	assert response["observation"] == "WeChat is already visible"
	assert response["thought"] == "No additional action is required"
	assert response["action"] == []
	assert response["comment"] == "微信已打开。"


def test_legacy_nonvisual_action_includes_control_id():
	response = normalize_app_response(
		{
			"Observation": "Start menu is open",
			"Thought": "Click WeChat",
			"Function": "click_input",
			"Args": {"button": "left"},
			"Status": "CONTINUE",
			"ControlLabel": "12",
		}
	)
	assert response["action"] == {
		"function": "click_input",
		"arguments": {"button": "left", "id": "12"},
		"status": "CONTINUE",
	}


def test_ufo_openai_runtime_limits_qwen_generation(tmp_path):
	path = tmp_path / "ufo" / "llm" / "openai.py"
	path.parent.mkdir(parents=True)
	path.write_text(
		'base_params = {\n                "n": 1,\n                **kwargs,\n'
		'                # "max_tokens": max_tokens,\n}\n'
		'            # Add generation parameters for non-reasoning models\n'
		'            if not self.config_llm.get("REASONING_MODEL", False):\n'
		'                pass\n',
		encoding="utf-8",
	)

	patch_ufo_openai_runtime(tmp_path)

	patched = path.read_text(encoding="utf-8")
	assert '"max_tokens": max_tokens,' in patched
	assert 'base_params["max_tokens"] = max_tokens' in patched
	assert '"enable_thinking": False' in patched
	assert UFO_MAX_TOKENS == 512


def test_ufo_device_server_source_accepts_legacy_nonvisual_response(tmp_path):
	path = (
		tmp_path
		/ "ufo"
		/ "agents"
		/ "processors"
		/ "strategies"
		/ "app_agent_processing_strategy.py"
	)
	path.parent.mkdir(parents=True)
	path.write_text(
		"            response_dict = agent.response_to_dict(response_text)\n\n"
		"            # Create structured response\n"
		"            parsed_response = AppAgentResponse.model_validate(response_dict)\n",
		encoding="utf-8",
	)

	patch_ufo_app_response_source(tmp_path)

	patched = path.read_text(encoding="utf-8")
	assert "I-ONE legacy nonvisual response compatibility." in patched
	assert "str(key).lower()" in patched
	assert "response_dict = normalized" in patched


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


def test_extract_answer_prefers_final_results_over_raw_constellation_event():
	result = {
		"status": "completed",
		"session_results": {
			"status": "FINISH",
			"final_results": [{"result": "微信已成功打开。"}],
		},
	}
	events = [
		{
			"event_type": "agent_response",
			"output_data": {"result": '{"tasks":{"task-1":{"status":"completed"}}}'},
		}
	]
	assert extract_answer(result, events) == "微信已成功打开。"


def test_nested_constellation_failure_overrides_completed_transport_status():
	result = {
		"status": "completed",
		"session_results": {
			"status": "FAIL",
			"final_constellation_stats": {
				"state": "failed",
				"total_tasks": 6,
				"task_status_counts": {"failed": 6},
			},
		},
		"constellation": {"state": "failed"},
	}
	assert result_failed(result) is True
	assert failure_message(result).startswith("任务执行失败：共尝试 6 个步骤，6 个失败。")


def test_unsure_device_evaluation_overrides_completed_transport_task():
	task = {
		"status": "completed",
		"result": {
			"status": "completed",
			"result": [
				{"request": "Open WeChat", "result": ""},
				{
					"reason": "The execution trajectory is empty.",
					"complete": "unsure",
					"type": "evaluation_result",
				},
			],
		},
	}
	assert task_execution_failed(task) is True


def test_successful_device_evaluation_is_not_failed():
	task = {
		"status": "completed",
		"result": {
			"status": "completed",
			"result": [{"complete": "yes", "type": "evaluation_result"}],
		},
	}
	assert task_execution_failed(task) is False


def test_raw_constellation_is_summarized_instead_of_returned():
	result = {
		"session_results": {},
	}
	events = [
		{
			"event_type": "agent_response",
			"output_data": {
				"result": '{"tasks":{"task-1":{"status":"failed"},"task-2":{"status":"pending"}}}'
			},
		}
	]
	assert extract_answer(result, events) == "任务执行失败：共执行 2 个步骤，其中 1 个失败。"


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


def test_probe_websocket_server_requires_switching_protocols(monkeypatch):
	class Connection:
		def __init__(self):
			self.request = b""

		def __enter__(self):
			return self

		def __exit__(self, *args):
			return None

		def settimeout(self, timeout):
			assert timeout == 5

		def sendall(self, request):
			self.request = request

		def recv(self, size):
			assert b"GET /ws?token=secret HTTP/1.1" in self.request
			return b"HTTP/1.1 101 Switching Protocols\r\n\r\n"

	connection = Connection()
	monkeypatch.setattr("app.runtime.socket.create_connection", lambda *args, **kwargs: connection)

	assert probe_websocket_server("127.0.0.1", 5000, "/ws?token=secret") is True


def test_probe_websocket_server_rejects_unresponsive_service(monkeypatch):
	def timeout(*args, **kwargs):
		raise TimeoutError

	monkeypatch.setattr("app.runtime.socket.create_connection", timeout)

	assert probe_websocket_server("127.0.0.1", 5000, "/ws?token=secret") is False


def test_constellation_heartbeat_uses_registered_role():
	assert heartbeat_client_type("task@windows-device") == "constellation"


def test_device_heartbeat_keeps_device_role():
	assert heartbeat_client_type("windows-device") == "device"


def test_public_device_keepalive_is_valid_aip_heartbeat():
	assert DEVICE_KEEPALIVE_INTERVAL_SECONDS < 60
	assert device_heartbeat_message() == '{"type":"heartbeat","status":"ok"}'


def test_gateway_allows_blocking_desktop_steps_to_answer_websocket_pings():
	dockerfile = OFFLINE_DOCKERFILE.read_text(encoding="utf-8")
	assert '"--ws-ping-timeout", "600"' in dockerfile
