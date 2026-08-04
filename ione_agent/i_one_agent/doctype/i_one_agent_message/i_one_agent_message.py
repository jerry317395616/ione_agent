from __future__ import annotations

import frappe
from frappe.model.document import Document


class IONEAgentMessage(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user
		self.visible = 1 if self.visible is None else self.visible
