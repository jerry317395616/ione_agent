from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
	return datetime.now(timezone.utc).isoformat()


class RunStore:
	def __init__(self, path: Path) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(path, check_same_thread=False)
		self.connection.row_factory = sqlite3.Row
		self.lock = threading.RLock()
		self._initialize()

	def _initialize(self) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS runs (
				  run_id TEXT PRIMARY KEY,
				  client_run_id TEXT NOT NULL UNIQUE,
				  session_id TEXT NOT NULL,
				  user_id TEXT NOT NULL,
				  request TEXT NOT NULL,
				  history_json TEXT NOT NULL,
				  status TEXT NOT NULL,
				  progress REAL NOT NULL DEFAULT 0,
				  current_stage TEXT,
				  answer TEXT,
				  error TEXT,
				  events_json TEXT NOT NULL DEFAULT '[]',
				  ufo_commit TEXT,
				  model TEXT,
				  created_at TEXT NOT NULL,
				  started_at TEXT,
				  completed_at TEXT,
				  elapsed_seconds REAL NOT NULL DEFAULT 0,
				  stop_requested INTEGER NOT NULL DEFAULT 0
				)
				"""
			)

	def create(self, payload: dict[str, Any], *, model: str, ufo_commit: str) -> dict[str, Any]:
		run_id = f"run_{uuid.uuid4().hex}"
		created_at = utc_now()
		with self.lock, self.connection:
			existing = self.connection.execute(
				"SELECT * FROM runs WHERE client_run_id = ?", (payload["client_run_id"],)
			).fetchone()
			if existing:
				return self._serialize(existing)
			self.connection.execute(
				"""
				INSERT INTO runs (
				  run_id, client_run_id, session_id, user_id, request, history_json,
				  status, progress, current_stage, events_json, ufo_commit, model, created_at
				) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, '[]', ?, ?, ?)
				""",
				(
					run_id,
					payload["client_run_id"],
					payload["session_id"],
					payload["user_id"],
					payload["request"],
					json.dumps(payload.get("history", []), ensure_ascii=False),
					"等待 UFO3 执行",
					ufo_commit,
					model,
					created_at,
				),
			)
		return self.get(run_id)

	def get(self, run_id: str) -> dict[str, Any] | None:
		with self.lock:
			row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
		return self._serialize(row) if row else None

	def recoverable(self) -> list[str]:
		with self.lock, self.connection:
			rows = self.connection.execute(
				"SELECT run_id FROM runs WHERE status IN ('queued', 'running') ORDER BY created_at"
			).fetchall()
			self.connection.execute(
				"UPDATE runs SET status='queued', progress=0, current_stage='网关重启后等待恢复' WHERE status='running'"
			)
		return [row["run_id"] for row in rows]

	def update(self, run_id: str, **values: Any) -> dict[str, Any]:
		allowed = {
			"status",
			"progress",
			"current_stage",
			"answer",
			"error",
			"ufo_commit",
			"model",
			"started_at",
			"completed_at",
			"elapsed_seconds",
			"stop_requested",
		}
		updates = {key: value for key, value in values.items() if key in allowed}
		if not updates:
			return self.get(run_id)
		clause = ", ".join(f"{key} = ?" for key in updates)
		with self.lock, self.connection:
			self.connection.execute(f"UPDATE runs SET {clause} WHERE run_id = ?", (*updates.values(), run_id))
		return self.get(run_id)

	def append_event(self, run_id: str, event: dict[str, Any]) -> None:
		with self.lock, self.connection:
			row = self.connection.execute(
				"SELECT events_json FROM runs WHERE run_id = ?", (run_id,)
			).fetchone()
			if not row:
				return
			events = json.loads(row["events_json"] or "[]")
			events.append(event)
			events = events[-200:]
			self.connection.execute(
				"UPDATE runs SET events_json = ? WHERE run_id = ?",
				(json.dumps(events, ensure_ascii=False), run_id),
			)

	def request_stop(self, run_id: str) -> dict[str, Any] | None:
		with self.lock, self.connection:
			self.connection.execute("UPDATE runs SET stop_requested=1 WHERE run_id = ?", (run_id,))
		return self.get(run_id)

	@staticmethod
	def _serialize(row: sqlite3.Row) -> dict[str, Any]:
		data = dict(row)
		data["history"] = json.loads(data.pop("history_json") or "[]")
		data["events"] = json.loads(data.pop("events_json") or "[]")
		data["stop_requested"] = bool(data["stop_requested"])
		return data
