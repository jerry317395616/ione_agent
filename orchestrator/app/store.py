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
			self.connection.execute("PRAGMA busy_timeout=15000")
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
			self._ensure_column("runs", "graph_version", "TEXT NOT NULL DEFAULT 'lead-agent-v2'")
			self._ensure_column("runs", "tenant", "TEXT NOT NULL DEFAULT 'manager.myyr.top'")
			self._ensure_column("runs", "iteration_count", "INTEGER NOT NULL DEFAULT 0")
			self._ensure_column("runs", "last_checkpoint_at", "TEXT")
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS tool_executions (
				 execution_id TEXT PRIMARY KEY,
				 idempotency_key TEXT NOT NULL UNIQUE,
				 run_id TEXT NOT NULL,
				 tool_call_id TEXT NOT NULL,
				 tool_name TEXT NOT NULL,
				 tool_version TEXT NOT NULL,
				 risk_level TEXT NOT NULL,
				 status TEXT NOT NULL,
				 attempt_count INTEGER NOT NULL DEFAULT 0,
				 arguments_json TEXT NOT NULL DEFAULT '{}',
				 result_json TEXT NOT NULL DEFAULT '{}',
				 error TEXT,
				 created_at TEXT NOT NULL,
				 started_at TEXT,
				 completed_at TEXT,
				 FOREIGN KEY(run_id) REFERENCES runs(run_id)
				)
				"""
			)
			self.connection.execute(
				"CREATE INDEX IF NOT EXISTS idx_tool_executions_run ON tool_executions(run_id, created_at)"
			)
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS model_calls (
				 call_id TEXT PRIMARY KEY,
				 run_id TEXT,
				 provider TEXT NOT NULL,
				 model TEXT NOT NULL,
				 purpose TEXT NOT NULL,
				 status TEXT NOT NULL,
				 request_hash TEXT NOT NULL,
				 response_preview TEXT,
				 error TEXT,
				 elapsed_ms INTEGER NOT NULL DEFAULT 0,
				 created_at TEXT NOT NULL
				)
				"""
			)
			self.connection.execute(
				"CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id, created_at)"
			)

	def _ensure_column(self, table: str, column: str, definition: str) -> None:
		columns = {row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")}
		if column not in columns:
			self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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
				(run_id,client_run_id,task_id,user_id,payload_json,status,stage,progress,current_stage,
				 graph_version,tenant,created_at)
				VALUES (?,?,?,?,?,'queued','queued',0,?,?,?,?)""",
				(
					run_id,
					payload["client_run_id"],
					payload["task_id"],
					payload["user_id"],
					json.dumps(payload, ensure_ascii=False),
					"等待执行",
					payload.get("graph_version") or "lead-agent-v2",
					payload.get("tenant") or "manager.myyr.top",
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
			"elapsed_seconds", "stop_requested", "graph_version", "tenant", "iteration_count",
			"last_checkpoint_at",
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

	def begin_tool_execution(
		self,
		*,
		run_id: str,
		tool_call_id: str,
		tool_name: str,
		tool_version: str,
		risk_level: str,
		arguments: dict[str, Any],
		idempotency_key: str,
	) -> dict[str, Any]:
		with self.lock, self.connection:
			row = self.connection.execute(
				"SELECT * FROM tool_executions WHERE idempotency_key=?", (idempotency_key,)
			).fetchone()
			if not row:
				execution_id = f"tool_{uuid.uuid4().hex}"
				self.connection.execute(
					"""INSERT INTO tool_executions
					(execution_id,idempotency_key,run_id,tool_call_id,tool_name,tool_version,risk_level,
					 status,attempt_count,arguments_json,created_at,started_at)
					VALUES (?,?,?,?,?,?,?,'running',1,?,?,?)""",
					(
						execution_id,
						idempotency_key,
						run_id,
						tool_call_id,
						tool_name,
						tool_version,
						risk_level,
						json.dumps(arguments, ensure_ascii=False),
						utc_now(),
						utc_now(),
					),
				)
				row = self.connection.execute(
					"SELECT * FROM tool_executions WHERE idempotency_key=?", (idempotency_key,)
				).fetchone()
			elif row["status"] != "completed":
				self.connection.execute(
					"""UPDATE tool_executions
					SET status='running',attempt_count=attempt_count+1,error=NULL,started_at=?
					WHERE idempotency_key=?""",
					(utc_now(), idempotency_key),
				)
				row = self.connection.execute(
					"SELECT * FROM tool_executions WHERE idempotency_key=?", (idempotency_key,)
				).fetchone()
		return self._serialize_execution(row)

	def complete_tool_execution(self, idempotency_key: str, result: dict[str, Any]) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"""UPDATE tool_executions
				SET status='completed',result_json=?,error=NULL,completed_at=? WHERE idempotency_key=?""",
				(json.dumps(result, ensure_ascii=False), utc_now(), idempotency_key),
			)

	def fail_tool_execution(self, idempotency_key: str, result: dict[str, Any], error: str) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"""UPDATE tool_executions
				SET status='failed',result_json=?,error=?,completed_at=? WHERE idempotency_key=?""",
				(json.dumps(result, ensure_ascii=False), error[:4000], utc_now(), idempotency_key),
			)

	def record_model_call(
		self,
		*,
		run_id: str | None,
		provider: str,
		model: str,
		purpose: str,
		status: str,
		request_hash: str,
		response_preview: str = "",
		error: str = "",
		elapsed_ms: int = 0,
	) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"""INSERT INTO model_calls
				(call_id,run_id,provider,model,purpose,status,request_hash,response_preview,error,elapsed_ms,created_at)
				VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
				(
					f"model_{uuid.uuid4().hex}",
					run_id,
					provider,
					model,
					purpose,
					status,
					request_hash,
					response_preview[:4000],
					error[:4000],
					elapsed_ms,
					utc_now(),
				),
			)

	def trace(self, run_id: str) -> dict[str, Any]:
		with self.lock:
			tool_rows = self.connection.execute(
				"SELECT * FROM tool_executions WHERE run_id=? ORDER BY created_at", (run_id,)
			).fetchall()
			model_rows = self.connection.execute(
				"SELECT * FROM model_calls WHERE run_id=? ORDER BY created_at", (run_id,)
			).fetchall()
		return {
			"tools": [self._serialize_execution(row) for row in tool_rows],
			"models": [dict(row) for row in model_rows],
		}

	def metrics(self) -> dict[str, int]:
		with self.lock:
			counts = {
				row["status"]: row["count"]
				for row in self.connection.execute(
					"SELECT status,COUNT(*) AS count FROM runs GROUP BY status"
				).fetchall()
			}
			tool_failures = self.connection.execute(
				"SELECT COUNT(*) FROM tool_executions WHERE status='failed'"
			).fetchone()[0]
			model_failures = self.connection.execute(
				"SELECT COUNT(*) FROM model_calls WHERE status='failed'"
			).fetchone()[0]
		return {
			"runs_queued": counts.get("queued", 0),
			"runs_running": counts.get("running", 0),
			"runs_completed": counts.get("completed", 0),
			"runs_failed": counts.get("failed", 0),
			"tool_failures": tool_failures,
			"model_failures": model_failures,
		}

	@staticmethod
	def _serialize_execution(row: sqlite3.Row) -> dict[str, Any]:
		data = dict(row)
		data["arguments"] = json.loads(data.pop("arguments_json") or "{}")
		data["result"] = json.loads(data.pop("result_json") or "{}")
		return data

	@staticmethod
	def serialize(row: sqlite3.Row) -> dict[str, Any]:
		data = dict(row)
		data["payload"] = json.loads(data.pop("payload_json") or "{}")
		data["result"] = json.loads(data.pop("result_json") or "{}")
		data["components"] = json.loads(data.pop("components_json") or "{}")
		data["events"] = json.loads(data.pop("events_json") or "[]")
		data["stop_requested"] = bool(data["stop_requested"])
		return data
