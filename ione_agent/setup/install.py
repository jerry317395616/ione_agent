from __future__ import annotations

import frappe

ROLES = ("I-ONE Agent User", "I-ONE Agent Manager")


def _ensure_roles() -> None:
	for role_name in ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)


def before_install() -> None:
	_ensure_roles()


def after_install() -> None:
	_ensure_roles()
	frappe.clear_cache()


def after_migrate() -> None:
	_ensure_roles()
	frappe.clear_cache()
