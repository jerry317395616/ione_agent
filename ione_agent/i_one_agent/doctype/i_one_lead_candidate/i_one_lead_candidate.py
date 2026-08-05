from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class IONELeadCandidate(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user

	def validate(self) -> None:
		self.relevance_score = max(0, min(100, flt(self.relevance_score)))
		self.confidence = max(0, min(100, flt(self.confidence)))
		if not self.fingerprint:
			identity = "|".join(
				str(value or "").strip().lower()
				for value in (self.source_url, self.project_number, self.title, self.purchaser)
			)
			self.fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()

