from __future__ import annotations

import frappe

MANAGER_ROLES = {"System Manager", "I-ONE Agent Manager"}
APP_ROLES = MANAGER_ROLES | {"I-ONE Agent User"}


def _user(user: str | None = None) -> str:
	return user or frappe.session.user


def _is_manager(user: str | None = None) -> bool:
	return bool(MANAGER_ROLES.intersection(frappe.get_roles(_user(user))))


def has_app_permission() -> bool:
	user = _user()
	return user not in {"Guest", ""} and bool(APP_ROLES.intersection(frappe.get_roles(user)))


def _owner_query(user: str | None = None) -> str:
	user = _user(user)
	if user == "Administrator" or _is_manager(user):
		return ""
	return f"`user` = {frappe.db.escape(user)}"


def session_query(user: str | None = None) -> str:
	return _owner_query(user)


def message_query(user: str | None = None) -> str:
	return _owner_query(user)


def run_query(user: str | None = None) -> str:
	return _owner_query(user)


def device_query(user: str | None = None) -> str:
	return _owner_query(user)


def pairing_query(user: str | None = None) -> str:
	return _owner_query(user)


def profile_query(user: str | None = None) -> str:
	return _owner_query(user)


def discovery_task_query(user: str | None = None) -> str:
	return _owner_query(user)


def candidate_query(user: str | None = None) -> str:
	return _owner_query(user)


def _document_permission(doc, user: str | None = None) -> bool:
	user = _user(user)
	return user == "Administrator" or _is_manager(user) or doc.user == user


def session_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def message_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def run_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def device_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def pairing_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def profile_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def discovery_task_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)


def candidate_permission(doc, user: str | None = None, **kwargs) -> bool:
	return _document_permission(doc, user)
