from __future__ import annotations

import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_ROOT))

from app.runtime import build_prompt, extract_answer  # noqa: E402


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

