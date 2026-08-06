from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver


class CheckpointManager:
	def __init__(self, *, data_dir: Path, database_url: str = "") -> None:
		self.backend = "sqlite-wal"
		self._context: Any = None
		self._connection: sqlite3.Connection | None = None
		if database_url:
			from langgraph.checkpoint.postgres import PostgresSaver

			self._context = PostgresSaver.from_conn_string(database_url)
			self.saver = self._context.__enter__()
			self.saver.setup()
			self.backend = "postgresql"
			return

		connection = sqlite3.connect(data_dir / "checkpoints.sqlite3", check_same_thread=False)
		connection.execute("PRAGMA journal_mode=WAL")
		connection.execute("PRAGMA synchronous=FULL")
		connection.execute("PRAGMA busy_timeout=15000")
		self._connection = connection
		self.saver = SqliteSaver(connection)

	def close(self) -> None:
		if self._context:
			self._context.__exit__(None, None, None)
			self._context = None
		if self._connection:
			self._connection.close()
			self._connection = None

	def __enter__(self) -> CheckpointManager:
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_value: BaseException | None,
		traceback: TracebackType | None,
	) -> None:
		self.close()
