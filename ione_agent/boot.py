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
DIFY_DESKTOP_ICON_DATA = {
	"label": "Dify",
	"bg_color": "gray",
	"link": "/dify",
	"link_type": "External",
	"app": "ione_agent",
	"icon_type": "App",
	"parent_icon": None,
	"icon": None,
	"link_to": "",
	"idx": 0,
	"standard": 1,
	"logo_url": "/assets/ione_agent/images/dify-logo.svg",
	"hidden": 0,
	"name": "Dify",
	"restrict_removal": 1,
	"icon_image": None,
}


def _app_name(item: Any) -> str | None:
	if isinstance(item, dict):
		return item.get("app_name")
	return getattr(item, "app_name", None)


def _desktop_icon_name(item: Any) -> str | None:
	if isinstance(item, dict):
		return item.get("name")
	return getattr(item, "name", None)


def extend_bootinfo(bootinfo) -> None:
	"""Expose Dify in both Frappe v17 Apps and Desktop Icons modes."""

	app_data = bootinfo.get("app_data")
	if app_data is None:
		app_data = []
		bootinfo["app_data"] = app_data
	elif not isinstance(app_data, list):
		app_data = list(app_data)
		bootinfo["app_data"] = app_data

	is_configured = bool(
		frappe.conf.get("ione_agent_dify_origin") and frappe.conf.get("ione_agent_dify_oauth_login_url")
	)
	if not is_configured or not has_dify_permission():
		bootinfo["app_data"] = [item for item in app_data if _app_name(item) != DIFY_APP_NAME]
		if "desktop_icons" in bootinfo:
			bootinfo["desktop_icons"] = [
				item for item in (bootinfo.get("desktop_icons") or []) if _desktop_icon_name(item) != "Dify"
			]
		return
	if not any(_app_name(item) == DIFY_APP_NAME for item in app_data):
		app_data.append({**DIFY_APP_DATA, "modules": [], "workspaces": []})

	if "desktop_icons" not in bootinfo:
		return
	desktop_icons = bootinfo.get("desktop_icons") or []
	if not isinstance(desktop_icons, list):
		desktop_icons = list(desktop_icons)
	bootinfo["desktop_icons"] = desktop_icons
	if not any(_desktop_icon_name(item) == "Dify" for item in desktop_icons):
		desktop_icons.append({**DIFY_DESKTOP_ICON_DATA})
