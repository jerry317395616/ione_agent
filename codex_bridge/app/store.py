from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


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

	def close(self) -> None:
		with self.lock:
			self.connection.close()

