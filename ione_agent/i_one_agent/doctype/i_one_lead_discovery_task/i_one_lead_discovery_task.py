from __future__ import annotations

import frappe
from frappe.model.document import Document


class IONELeadDiscoveryTask(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user
		self.status = self.status or "等待执行"

