from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt


class IONELeadDiscoveryProfile(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user

	def validate(self) -> None:
		self.days_back = max(1, min(365, cint(self.days_back or 30)))
		self.maximum_results = max(1, min(200, cint(self.maximum_results or 30)))
		self.score_threshold = max(0, min(100, flt(self.score_threshold or 70)))
		self.profile_name = (self.profile_name or "默认获客配置").strip()

