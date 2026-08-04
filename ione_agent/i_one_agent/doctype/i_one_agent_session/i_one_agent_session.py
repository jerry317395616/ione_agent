from __future__ import annotations

import frappe
from frappe.model.document import Document


class IONEAgentSession(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user
		self.status = self.status or "Active"

	def validate(self) -> None:
		if not self.title:
			self.title = "新对话"
