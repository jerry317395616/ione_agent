from __future__ import annotations

from typing import Any

import frappe

from ione_agent.permissions import has_dify_permission

DIFY_APP_NAME = "dify_launcher"
DIFY_APP_DATA = {
	"on_apps_screen": True,
	"sequence_id": 110,
	"app_name": DIFY_APP_NAME,
	"app_title": "Dify",
	"app_route": "/dify",
	"app_logo_url": "/assets/ione_agent/images/dify-logo.svg",
	"modules": [],
	"workspaces": [],
}


def _app_name(item: Any) -> str | None:
	if isinstance(item, dict):
		return item.get("app_name")
	return getattr(item, "app_name", None)


def extend_bootinfo(bootinfo) -> None:
	"""Expose the standalone Dify launcher as a permission-gated virtual Frappe v17 app."""

	app_data = bootinfo.get("app_data")
	if app_data is None:
		app_data = []
		bootinfo["app_data"] = app_data
	elif not isinstance(app_data, list):
		app_data = list(app_data)
		bootinfo["app_data"] = app_data

	is_configured = bool(
		frappe.conf.get("ione_agent_dify_origin")
		and frappe.conf.get("ione_agent_dify_oauth_login_url")
	)
	if not is_configured or not has_dify_permission():
		bootinfo["app_data"] = [item for item in app_data if _app_name(item) != DIFY_APP_NAME]
		return
	if any(_app_name(item) == DIFY_APP_NAME for item in app_data):
		return
	app_data.append({**DIFY_APP_DATA, "modules": [], "workspaces": []})
