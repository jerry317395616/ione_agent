from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe.model.document import Document

DEFAULT_INTEGRATION_ROLES = ("System Manager", "I-ONE Agent Manager")


def _integration_user_name(site: str) -> str:
	safe_site = "".join(character for character in site.lower() if character.isalnum() or character in ".-")
	return f"ione-agent@{safe_site}"


def _ensure_roles(user: Document, roles: Iterable[str]) -> None:
	existing = {row.role for row in user.roles}
	for role in roles:
		if role and frappe.db.exists("Role", role) and role not in existing:
			user.append("roles", {"role": role})


def create_integration_credentials(roles: list[str] | None = None) -> dict[str, str]:
	"""Create or rotate the host companion's dedicated Frappe API credentials.

	This function is intentionally not whitelisted. It is only called locally by the
	privileged host installer through ``bench execute``. The returned secret must be
	written directly to the protected systemd environment file and never logged.
	"""

	site = str(frappe.local.site or "").strip()
	if not site:
		raise RuntimeError("A Frappe site context is required")

	user_name = _integration_user_name(site)
	if frappe.db.exists("User", user_name):
		user = frappe.get_doc("User", user_name)
		user.enabled = 1
		user.user_type = "System User"
	else:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": user_name,
				"first_name": "I-ONE Agent",
				"last_name": site,
				"enabled": 1,
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		user.insert(ignore_permissions=True)

	_ensure_roles(user, roles or list(DEFAULT_INTEGRATION_ROLES))
	api_key = frappe.generate_hash(length=20)
	api_secret = frappe.generate_hash(length=40)
	user.api_key = api_key
	user.api_secret = api_secret
	user.save(ignore_permissions=True)
	frappe.db.commit()

	return {"user": user_name, "api_key": api_key, "api_secret": api_secret}
