from __future__ import annotations

from typing import Any

import frappe

from ione_agent.permissions import has_app_permission, has_dify_permission

DIFY_APP_NAME = "dify_launcher"
HARNESS_APP_NAME = "ione_harness_launcher"
IONE_WORKSPACE_TITLE = "I-ONE"
IONE_WORKSPACE_LOGO = "/assets/ione_agent/images/ione-workspace-logo.svg"
HARNESS_TITLE = "IONE Harness"
HARNESS_ROUTE = "/api/method/ione_core.harness_auth.launch"
DIFY_APP_DATA = {
	"on_apps_screen": True,
	"sequence_id": 110,
	"app_name": DIFY_APP_NAME,
	"app_title": IONE_WORKSPACE_TITLE,
	"app_route": "/dify",
	"app_logo_url": IONE_WORKSPACE_LOGO,
	"modules": [],
	"workspaces": [],
}
DIFY_DESKTOP_ICON_DATA = {
	"label": IONE_WORKSPACE_TITLE,
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
	"logo_url": IONE_WORKSPACE_LOGO,
	"hidden": 0,
	"name": IONE_WORKSPACE_TITLE,
	"restrict_removal": 1,
	"icon_image": None,
}
HARNESS_APP_DATA = {
	"on_apps_screen": True,
	"sequence_id": 111,
	"app_name": HARNESS_APP_NAME,
	"app_title": HARNESS_TITLE,
	"app_route": HARNESS_ROUTE,
	"app_logo_url": IONE_WORKSPACE_LOGO,
	"modules": [],
	"workspaces": [],
}
HARNESS_DESKTOP_ICON_DATA = {
	"label": HARNESS_TITLE,
	"bg_color": "gray",
	"link": HARNESS_ROUTE,
	"link_type": "External",
	"app": "ione_agent",
	"icon_type": "App",
	"parent_icon": None,
	"icon": None,
	"link_to": "",
	"idx": 0,
	"standard": 1,
	"logo_url": IONE_WORKSPACE_LOGO,
	"hidden": 0,
	"name": HARNESS_TITLE,
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


def _desktop_icon_link(item: Any) -> str | None:
	if isinstance(item, dict):
		return item.get("link")
	return getattr(item, "link", None)


def _is_workspace_launcher_icon(item: Any) -> bool:
	return _desktop_icon_link(item) == "/dify" or _desktop_icon_name(item) == "Dify"


def _is_harness_launcher_icon(item: Any) -> bool:
	return _desktop_icon_link(item) == HARNESS_ROUTE or _desktop_icon_name(item) == HARNESS_TITLE


def extend_bootinfo(bootinfo) -> None:
	"""Expose I-ONE launchers in Frappe v17 Apps and Desktop Icons modes."""

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
	app_data = [
		item
		for item in app_data
		if _app_name(item) not in {DIFY_APP_NAME, HARNESS_APP_NAME}
	]
	bootinfo["app_data"] = app_data
	if "desktop_icons" in bootinfo:
		desktop_icons = list(bootinfo.get("desktop_icons") or [])
		bootinfo["desktop_icons"] = [
			item
			for item in desktop_icons
			if not _is_workspace_launcher_icon(item) and not _is_harness_launcher_icon(item)
		]

	if has_app_permission():
		app_data.append({**HARNESS_APP_DATA, "modules": [], "workspaces": []})
		if "desktop_icons" in bootinfo:
			bootinfo["desktop_icons"].append({**HARNESS_DESKTOP_ICON_DATA})

	if is_configured and has_dify_permission():
		app_data.append({**DIFY_APP_DATA, "modules": [], "workspaces": []})
		if "desktop_icons" in bootinfo:
			bootinfo["desktop_icons"].append({**DIFY_DESKTOP_ICON_DATA})
