from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class ConversationStore:
	def __init__(self, path: Path) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(path, check_same_thread=False)
		self.lock = threading.RLock()
		with self.lock, self.connection:
			self.connection.execute("PRAGMA journal_mode=WAL")
			self.connection.execute("PRAGMA synchronous=FULL")
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS conversations (
				  librechat_user_id TEXT NOT NULL,
				  conversation_id TEXT NOT NULL,
				  codex_thread_id TEXT NOT NULL,
				  updated_at INTEGER NOT NULL,
				  PRIMARY KEY (librechat_user_id, conversation_id)
				)
				"""
			)
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS recipe_imports (
				  task_id TEXT PRIMARY KEY,
				  librechat_user_id TEXT NOT NULL,
				  conversation_id TEXT NOT NULL,
				  source_sha256 TEXT NOT NULL,
				  status TEXT NOT NULL,
				  payload_json TEXT NOT NULL,
				  result_json TEXT,
				  created_at INTEGER NOT NULL,
				  updated_at INTEGER NOT NULL
				)
				"""
			)
			self.connection.execute(
				"""
				CREATE INDEX IF NOT EXISTS recipe_imports_conversation_idx
				ON recipe_imports (librechat_user_id, conversation_id, updated_at DESC)
				"""
			)

	def get(self, user_id: str, conversation_id: str) -> str | None:
		with self.lock:
			row = self.connection.execute(
				"SELECT codex_thread_id FROM conversations WHERE librechat_user_id=? AND conversation_id=?",
				(user_id, conversation_id),
			).fetchone()
		return str(row[0]) if row else None

	def set(self, user_id: str, conversation_id: str, thread_id: str) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"""
				INSERT INTO conversations
				  (librechat_user_id, conversation_id, codex_thread_id, updated_at)
				VALUES (?, ?, ?, ?)
				ON CONFLICT(librechat_user_id, conversation_id) DO UPDATE SET
				  codex_thread_id=excluded.codex_thread_id,
				  updated_at=excluded.updated_at
				""",
				(user_id, conversation_id, thread_id, int(time.time())),
			)

	def delete(self, user_id: str, conversation_id: str) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"DELETE FROM conversations WHERE librechat_user_id=? AND conversation_id=?",
				(user_id, conversation_id),
			)

	def count(self) -> int:
		with self.lock:
			return int(self.connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])

	def save_recipe_import(
		self,
		user_id: str,
		conversation_id: str,
		payload: dict[str, Any],
	) -> None:
		now = int(time.time())
		with self.lock, self.connection:
			self.connection.execute(
				"""
				UPDATE recipe_imports SET status='superseded', updated_at=?
				WHERE librechat_user_id=? AND conversation_id=? AND status='parsed'
				""",
				(now, user_id, conversation_id),
			)
			self.connection.execute(
				"""
				INSERT INTO recipe_imports
				  (task_id, librechat_user_id, conversation_id, source_sha256, status,
				   payload_json, created_at, updated_at)
				VALUES (?, ?, ?, ?, 'parsed', ?, ?, ?)
				""",
				(
					str(payload["task_id"]),
					user_id,
					conversation_id,
					str(payload["source_sha256"]),
					json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
					now,
					now,
				),
			)

	def latest_recipe_import(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
		with self.lock:
			row = self.connection.execute(
				"""
				SELECT payload_json, status, result_json
				FROM recipe_imports
				WHERE librechat_user_id=? AND conversation_id=?
				  AND status IN ('parsed', 'committed', 'failed')
				ORDER BY updated_at DESC LIMIT 1
				""",
				(user_id, conversation_id),
			).fetchone()
		if not row:
			return None
		payload = json.loads(str(row[0]))
		payload["status"] = str(row[1])
		if row[2]:
			payload["commit_result"] = json.loads(str(row[2]))
		return payload

	def finish_recipe_import(
		self,
		task_id: str,
		*,
		status: str,
		result: dict[str, Any],
	) -> None:
		if status not in {"committed", "failed"}:
			raise ValueError("Invalid recipe import status")
		with self.lock, self.connection:
			self.connection.execute(
				"""
				UPDATE recipe_imports SET status=?, result_json=?, updated_at=? WHERE task_id=?
				""",
				(
					status,
					json.dumps(result, ensure_ascii=False, separators=(",", ":")),
					int(time.time()),
					task_id,
				),
			)

	def delete_recipe_imports(self, user_id: str, conversation_id: str) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"DELETE FROM recipe_imports WHERE librechat_user_id=? AND conversation_id=?",
				(user_id, conversation_id),
			)

	def close(self) -> None:
		with self.lock:
			self.connection.close()
