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
		with self.lock, self.connection:
			self.connection.execute("PRAGMA journal_mode=WAL")
			self.connection.execute("PRAGMA synchronous=FULL")
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS runs (
				 run_id TEXT PRIMARY KEY,
				 client_run_id TEXT NOT NULL UNIQUE,
				 task_id TEXT NOT NULL,
				 user_id TEXT NOT NULL,
				 payload_json TEXT NOT NULL,
				 status TEXT NOT NULL,
				 stage TEXT NOT NULL,
				 progress REAL NOT NULL DEFAULT 0,
				 current_stage TEXT,
				 result_json TEXT NOT NULL DEFAULT '{}',
				 error TEXT,
				 components_json TEXT NOT NULL DEFAULT '{}',
				 events_json TEXT NOT NULL DEFAULT '[]',
				 created_at TEXT NOT NULL,
				 started_at TEXT,
				 completed_at TEXT,
				 elapsed_seconds REAL NOT NULL DEFAULT 0,
				 stop_requested INTEGER NOT NULL DEFAULT 0
				)
				"""
			)

	def create(self, payload: dict[str, Any]) -> dict[str, Any]:
		with self.lock, self.connection:
			existing = self.connection.execute(
				"SELECT * FROM runs WHERE client_run_id=?", (payload["client_run_id"],)
			).fetchone()
			if existing:
				return self.serialize(existing)
			run_id = f"lead_{uuid.uuid4().hex}"
			self.connection.execute(
				"""INSERT INTO runs
				(run_id,client_run_id,task_id,user_id,payload_json,status,stage,progress,current_stage,created_at)
				VALUES (?,?,?,?,?,'queued','queued',0,?,?)""",
				(
					run_id,
					payload["client_run_id"],
					payload["task_id"],
					payload["user_id"],
					json.dumps(payload, ensure_ascii=False),
					"等待执行",
					utc_now(),
				),
			)
		return self.get(run_id)

	def get(self, run_id: str) -> dict[str, Any] | None:
		with self.lock:
			row = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
			return self.serialize(row) if row else None

	def recoverable(self) -> list[str]:
		with self.lock, self.connection:
			rows = self.connection.execute(
				"SELECT run_id FROM runs WHERE status IN ('queued','running') ORDER BY created_at"
			).fetchall()
			self.connection.execute(
				"UPDATE runs SET status='queued',stage='queued',progress=0,current_stage='服务重启后等待恢复' WHERE status='running'"
			)
		return [row["run_id"] for row in rows]

	def update(self, run_id: str, **values: Any) -> dict[str, Any] | None:
		json_fields = {"result": "result_json", "components": "components_json", "events": "events_json"}
		columns = {
			"status", "stage", "progress", "current_stage", "error", "started_at", "completed_at",
			"elapsed_seconds", "stop_requested",
		}
		updates: dict[str, Any] = {}
		for key, value in values.items():
			if key in json_fields:
				updates[json_fields[key]] = json.dumps(value, ensure_ascii=False)
			elif key in columns:
				updates[key] = value
		if not updates:
			return self.get(run_id)
		clause = ", ".join(f"{key}=?" for key in updates)
		with self.lock, self.connection:
			self.connection.execute(f"UPDATE runs SET {clause} WHERE run_id=?", (*updates.values(), run_id))
		return self.get(run_id)

	def stage(self, run_id: str, stage: str, progress: int, message: str, **components: str) -> None:
		run = self.get(run_id)
		current_components = dict((run or {}).get("components") or {})
		current_components.update(components)
		events = list((run or {}).get("events") or [])[-99:]
		events.append({"time": utc_now(), "stage": stage, "message": message})
		self.update(
			run_id,
			status="running",
			stage=stage,
			progress=progress,
			current_stage=message,
			components=current_components,
			events=events,
		)

	def request_stop(self, run_id: str) -> dict[str, Any] | None:
		return self.update(run_id, stop_requested=1, current_stage="正在安全停止")

	@staticmethod
	def serialize(row: sqlite3.Row) -> dict[str, Any]:
		data = dict(row)
		data["payload"] = json.loads(data.pop("payload_json") or "{}")
		data["result"] = json.loads(data.pop("result_json") or "{}")
		data["components"] = json.loads(data.pop("components_json") or "{}")
		data["events"] = json.loads(data.pop("events_json") or "[]")
		data["stop_requested"] = bool(data["stop_requested"])
		return data
