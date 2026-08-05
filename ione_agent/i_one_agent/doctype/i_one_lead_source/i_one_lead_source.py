from __future__ import annotations

import frappe
from frappe.model.document import Document


class IONELeadSource(Document):
	def before_insert(self) -> None:
		self.user = self.user or frappe.session.user

	def validate(self) -> None:
		self.source_name = (self.source_name or "").strip()
		self.base_url = (self.base_url or "").strip().rstrip("/")
		if self.base_url and not self.base_url.startswith(("http://", "https://")):
			frappe.throw("来源地址必须以 http:// 或 https:// 开头。")

