from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.store import utc_now


def token_digest(token: str) -> str:
	return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceStore:
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
				CREATE TABLE IF NOT EXISTS devices (
				  device_id TEXT PRIMARY KEY,
				  device_name TEXT NOT NULL,
				  user_id TEXT NOT NULL,
				  token_hash TEXT NOT NULL,
				  platform TEXT NOT NULL,
				  client_version TEXT,
				  capabilities_json TEXT NOT NULL DEFAULT '[]',
				  status TEXT NOT NULL DEFAULT 'offline',
				  created_at TEXT NOT NULL,
				  last_seen_at TEXT,
				  revoked INTEGER NOT NULL DEFAULT 0
				)
				"""
			)
			self.connection.execute(
				"UPDATE devices SET status = 'offline' WHERE revoked = 0"
			)

	def register(self, payload: dict[str, Any]) -> dict[str, Any]:
		now = utc_now()
		values = (
			payload["device_id"],
			payload["device_name"],
			payload["user_id"],
			token_digest(payload["device_token"]),
			payload.get("platform", "windows"),
			payload.get("client_version", ""),
			json.dumps(payload.get("capabilities", []), ensure_ascii=False),
			now,
		)
		with self.lock, self.connection:
			self.connection.execute(
				"""
				INSERT INTO devices (
				  device_id, device_name, user_id, token_hash, platform,
				  client_version, capabilities_json, created_at, revoked, status
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'offline')
				ON CONFLICT(device_id) DO UPDATE SET
				  device_name=excluded.device_name,
				  user_id=excluded.user_id,
				  token_hash=excluded.token_hash,
				  platform=excluded.platform,
				  client_version=excluded.client_version,
				  capabilities_json=excluded.capabilities_json,
				  revoked=0,
				  status='offline'
				""",
				values,
			)
		return self.get(payload["device_id"])

	def get(self, device_id: str) -> dict[str, Any] | None:
		with self.lock:
			row = self.connection.execute(
				"SELECT * FROM devices WHERE device_id = ?", (device_id,)
			).fetchone()
		return self._serialize(row) if row else None

	def active(self) -> list[dict[str, Any]]:
		with self.lock:
			rows = self.connection.execute(
				"SELECT * FROM devices WHERE revoked = 0 ORDER BY created_at"
			).fetchall()
		return [self._serialize(row) for row in rows]

	def list(self) -> list[dict[str, Any]]:
		with self.lock:
			rows = self.connection.execute(
				"SELECT * FROM devices ORDER BY created_at DESC"
			).fetchall()
		return [self._serialize(row) for row in rows]

	def authenticate(self, device_id: str, token: str) -> bool:
		device = self.get(device_id)
		return bool(
			device
			and not device["revoked"]
			and hmac.compare_digest(device["token_hash"], token_digest(token))
		)

	def authenticate_token(self, token: str) -> dict[str, Any] | None:
		digest = token_digest(token)
		with self.lock:
			rows = self.connection.execute(
				"SELECT * FROM devices WHERE revoked = 0"
			).fetchall()
		for row in rows:
			if hmac.compare_digest(row["token_hash"], digest):
				return self._serialize(row)
		return None

	def set_status(self, device_id: str, status: str) -> None:
		with self.lock, self.connection:
			self.connection.execute(
				"UPDATE devices SET status = ?, last_seen_at = ? WHERE device_id = ?",
				(status, utc_now(), device_id),
			)

	def revoke(self, device_id: str) -> dict[str, Any] | None:
		with self.lock, self.connection:
			self.connection.execute(
				"UPDATE devices SET revoked = 1, status = 'revoked' WHERE device_id = ?",
				(device_id,),
			)
		return self.get(device_id)

	@staticmethod
	def _serialize(row: sqlite3.Row) -> dict[str, Any]:
		data = dict(row)
		data["capabilities"] = json.loads(data.pop("capabilities_json") or "[]")
		data["revoked"] = bool(data["revoked"])
		return data
