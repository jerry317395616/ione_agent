from __future__ import annotations

import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_ROOT))

from app.store import RunStore  # noqa: E402


def payload(client_run_id="local-run-1"):
	return {
		"client_run_id": client_run_id,
		"session_id": "session-1",
		"user_id": "user@example.com",
		"request": "检查库存",
		"history": [{"role": "user", "content": "先看库存"}],
	}


def test_store_create_is_idempotent(tmp_path):
	store = RunStore(tmp_path / "runs.sqlite3")
	first = store.create(payload(), model="qwen-test", ufo_commit="abc")
	second = store.create(payload(), model="qwen-test", ufo_commit="abc")
	assert first["run_id"] == second["run_id"]
	assert first["status"] == "queued"


def test_store_persists_events_and_terminal_state(tmp_path):
	store = RunStore(tmp_path / "runs.sqlite3")
	run = store.create(payload("local-run-2"), model="qwen-test", ufo_commit="abc")
	store.append_event(run["run_id"], {"event_type": "agent_output", "data": {"message": "完成"}})
	store.update(run["run_id"], status="completed", answer="已完成", progress=100)
	saved = store.get(run["run_id"])
	assert saved["answer"] == "已完成"
	assert saved["progress"] == 100
	assert saved["events"][0]["event_type"] == "agent_output"


def test_recoverable_run_is_returned_to_queue(tmp_path):
	store = RunStore(tmp_path / "runs.sqlite3")
	run = store.create(payload("local-run-3"), model="qwen-test", ufo_commit="abc")
	store.update(run["run_id"], status="running", progress=30)
	assert store.recoverable() == [run["run_id"]]
	assert store.get(run["run_id"])["status"] == "queued"

