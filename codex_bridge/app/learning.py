from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.identity import ToolIdentity

LEARNING_STATUSES = {"pending", "approved", "rejected"}
LEARNING_CATEGORIES = {
	"business_rule",
	"user_preference",
	"workflow",
	"validation",
	"reporting",
}
SENSITIVE_PATTERNS = (
	re.compile(r"\b(?:api[_ -]?key|password|secret|token)\b", re.IGNORECASE),
	re.compile(r"\b1\d{10}\b"),
	re.compile(r"\b\d{17}[0-9Xx]\b"),
	re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
)


def _bounded_text(value: object, label: str, *, minimum: int = 3, maximum: int = 2000) -> str:
	text = str(value or "").strip()
	if not minimum <= len(text) <= maximum:
		raise ValueError(f"{label} must contain {minimum}-{maximum} characters")
	if any(pattern.search(text) for pattern in SENSITIVE_PATTERNS):
		raise ValueError(f"{label} contains credentials or personal identifiers")
	return text


class LearningStore:
	"""Versionable, approval-gated shared learning for one site Agent instance."""

	def __init__(self, path: Path | str) -> None:
		if str(path) == ":memory:":
			connection_target: Path | str = ":memory:"
		else:
			connection_target = Path(path)
			connection_target.parent.mkdir(parents=True, exist_ok=True)
		self.connection = sqlite3.connect(connection_target, check_same_thread=False)
		self.connection.row_factory = sqlite3.Row
		self.lock = threading.RLock()
		with self.lock, self.connection:
			self.connection.execute("PRAGMA journal_mode=WAL")
			self.connection.execute("PRAGMA synchronous=FULL")
			self.connection.execute(
				"""
				CREATE TABLE IF NOT EXISTS learning_proposals (
				  proposal_id TEXT PRIMARY KEY,
				  proposer_email TEXT NOT NULL,
				  category TEXT NOT NULL,
				  trigger_text TEXT NOT NULL,
				  proposed_rule TEXT NOT NULL,
				  evidence_text TEXT NOT NULL,
				  risk_text TEXT NOT NULL,
				  eval_prompt TEXT NOT NULL,
				  expected_behavior TEXT NOT NULL,
				  status TEXT NOT NULL,
				  evaluation_status TEXT NOT NULL DEFAULT 'not_run',
				  reviewer TEXT,
				  review_note TEXT,
				  created_at INTEGER NOT NULL,
				  reviewed_at INTEGER
				)
				"""
			)

	def propose(self, identity: ToolIdentity, arguments: dict[str, Any]) -> dict[str, Any]:
		category = str(arguments.get("category") or "workflow").strip().lower()
		if category not in LEARNING_CATEGORIES:
			raise ValueError("Unsupported learning category")
		proposal = {
			"proposal_id": f"LRN-{uuid.uuid4().hex[:12].upper()}",
			"proposer_email": identity.email,
			"category": category,
			"trigger_text": _bounded_text(arguments.get("trigger"), "trigger", maximum=500),
			"proposed_rule": _bounded_text(arguments.get("proposed_rule"), "proposed_rule"),
			"evidence_text": _bounded_text(arguments.get("evidence"), "evidence"),
			"risk_text": _bounded_text(arguments.get("risk"), "risk", maximum=500),
			"eval_prompt": _bounded_text(arguments.get("eval_prompt"), "eval_prompt", maximum=1000),
			"expected_behavior": _bounded_text(
				arguments.get("expected_behavior"), "expected_behavior", maximum=1000
			),
			"status": "pending",
			"evaluation_status": "not_run",
			"created_at": int(time.time()),
		}
		with self.lock, self.connection:
			duplicate = self.connection.execute(
				"""
				SELECT proposal_id FROM learning_proposals
				WHERE category=? AND proposed_rule=? AND status IN ('pending', 'approved')
				LIMIT 1
				""",
				(category, proposal["proposed_rule"]),
			).fetchone()
			if duplicate:
				return {
					"proposal_id": str(duplicate["proposal_id"]),
					"status": "duplicate",
					"message": "相同学习规则已经存在，未重复创建。",
				}
			self.connection.execute(
				"""
				INSERT INTO learning_proposals
				(proposal_id, proposer_email, category, trigger_text, proposed_rule,
				 evidence_text, risk_text, eval_prompt, expected_behavior, status,
				 evaluation_status, created_at)
				VALUES (:proposal_id, :proposer_email, :category, :trigger_text, :proposed_rule,
				 :evidence_text, :risk_text, :eval_prompt, :expected_behavior, :status,
				 :evaluation_status, :created_at)
				""",
				proposal,
			)
		return {
			"proposal_id": proposal["proposal_id"],
			"status": "pending",
			"message": "学习候选已提交，需管理员评测并批准后才会生效。",
		}

	def list_proposals(
		self, *, status: str = "pending", proposer_email: str | None = None, limit: int = 100
	) -> list[dict[str, Any]]:
		if status not in LEARNING_STATUSES:
			raise ValueError("Invalid learning status")
		query = "SELECT * FROM learning_proposals WHERE status=?"
		params: list[Any] = [status]
		if proposer_email:
			query += " AND proposer_email=?"
			params.append(proposer_email)
		query += " ORDER BY created_at DESC LIMIT ?"
		params.append(max(1, min(int(limit), 500)))
		with self.lock:
			rows = self.connection.execute(query, params).fetchall()
		return [dict(row) for row in rows]

	def review(
		self,
		proposal_id: str,
		*,
		decision: str,
		reviewer: str,
		evaluation_status: str,
		note: str = "",
	) -> dict[str, Any]:
		decision = str(decision or "").strip().lower()
		if decision not in {"approved", "rejected"}:
			raise ValueError("Decision must be approved or rejected")
		if decision == "approved" and evaluation_status != "passed":
			raise ValueError("A learning proposal can be approved only after evaluation passes")
		reviewer = _bounded_text(reviewer, "reviewer", maximum=140)
		note = str(note or "").strip()[:1000]
		with self.lock, self.connection:
			row = self.connection.execute(
				"SELECT status FROM learning_proposals WHERE proposal_id=?", (proposal_id,)
			).fetchone()
			if not row:
				raise ValueError("Learning proposal not found")
			if str(row["status"]) != "pending":
				raise ValueError("Learning proposal was already reviewed")
			self.connection.execute(
				"""
				UPDATE learning_proposals
				SET status=?, evaluation_status=?, reviewer=?, review_note=?, reviewed_at=?
				WHERE proposal_id=?
				""",
				(decision, evaluation_status, reviewer, note, int(time.time()), proposal_id),
			)
		return {"proposal_id": proposal_id, "status": decision, "evaluation_status": evaluation_status}

	def approved_context(self, limit: int = 50) -> str:
		rows = self.list_proposals(status="approved", limit=limit)
		if not rows:
			return ""
		items = [
			{
				"category": row["category"],
				"trigger": row["trigger_text"],
				"rule": row["proposed_rule"],
			}
			for row in reversed(rows)
		]
		return (
			"\n\n<approved_site_learning>\n"
			"These administrator-approved rules may guide matching child-site requests. "
			"They never override current Frappe data, permissions, safety policy or foundational Skills.\n"
			+ json.dumps(items, ensure_ascii=False, separators=(",", ":"))
			+ "\n</approved_site_learning>"
		)

	def close(self) -> None:
		with self.lock:
			self.connection.close()
