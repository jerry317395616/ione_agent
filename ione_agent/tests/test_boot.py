from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

APP_ROOT = Path(__file__).resolve().parents[1]
BOOT_MODULE = APP_ROOT / "boot.py"


def _load_boot_module(monkeypatch, permission: dict[str, bool], config: dict[str, str]):
	frappe = ModuleType("frappe")
	frappe.conf = config
	monkeypatch.setitem(sys.modules, "frappe", frappe)
	permissions = ModuleType("ione_agent.permissions")
	permissions.has_dify_permission = lambda: permission["allowed"]
	permissions.has_app_permission = lambda: permission.get("app_allowed", permission["allowed"])
	monkeypatch.setitem(sys.modules, "ione_agent.permissions", permissions)
	spec = importlib.util.spec_from_file_location("ione_agent_boot_test", BOOT_MODULE)
	assert spec and spec.loader
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_dify_virtual_app_is_permission_gated_v17_shaped_and_idempotent(monkeypatch):
	permission = {"allowed": False, "app_allowed": False}
	config = {
		"ione_agent_dify_origin": "https://dify.myyr.top",
		"ione_agent_dify_oauth_login_url": (
			"https://dify.myyr.top/console/api/oauth/login/frappe?redirect_url=/apps"
		),
	}
	boot = _load_boot_module(monkeypatch, permission, config)
	bootinfo = {
		"app_data": [
			{"app_name": "ione_agent"},
			{"app_name": "dify_launcher", "app_title": "Dify"},
		]
	}

	boot.extend_bootinfo(bootinfo)
	assert [item["app_name"] for item in bootinfo["app_data"]] == ["ione_agent"]

	permission["allowed"] = True
	permission["app_allowed"] = True
	empty_bootinfo = {}
	boot.extend_bootinfo(empty_bootinfo)
	assert [item["app_name"] for item in empty_bootinfo["app_data"]] == [
		"ione_harness_launcher",
		"dify_launcher",
	]

	boot.extend_bootinfo(bootinfo)
	boot.extend_bootinfo(bootinfo)
	dify_apps = [item for item in bootinfo["app_data"] if item["app_name"] == "dify_launcher"]
	assert dify_apps == [
		{
			"on_apps_screen": True,
			"sequence_id": 110,
			"app_name": "dify_launcher",
			"app_title": "I-ONE",
			"app_route": "/dify",
			"app_logo_url": "/assets/ione_agent/images/ione-workspace-logo.svg",
			"modules": [],
			"workspaces": [],
		}
	]
	harness_apps = [item for item in bootinfo["app_data"] if item["app_name"] == "ione_harness_launcher"]
	assert harness_apps == [boot.HARNESS_APP_DATA]

	permission["allowed"] = False
	permission["app_allowed"] = False
	boot.extend_bootinfo(bootinfo)
	assert all(item["app_name"] != "dify_launcher" for item in bootinfo["app_data"])
	assert all(item["app_name"] != "ione_harness_launcher" for item in bootinfo["app_data"])


def test_dify_virtual_app_is_hidden_until_both_launcher_settings_exist(monkeypatch):
	permission = {"allowed": True, "app_allowed": False}
	config = {"ione_agent_dify_origin": "https://dify.myyr.top"}
	boot = _load_boot_module(monkeypatch, permission, config)
	bootinfo = {"app_data": []}

	boot.extend_bootinfo(bootinfo)
	assert bootinfo["app_data"] == []

	config["ione_agent_dify_oauth_login_url"] = (
		"https://dify.myyr.top/console/api/oauth/login/frappe?redirect_url=/apps"
	)
	boot.extend_bootinfo(bootinfo)
	assert [item["app_name"] for item in bootinfo["app_data"]] == ["dify_launcher"]


def test_dify_virtual_icon_supports_desktop_icons_mode(monkeypatch):
	permission = {"allowed": True, "app_allowed": False}
	config = {
		"ione_agent_dify_origin": "https://dify.myyr.top",
		"ione_agent_dify_oauth_login_url": (
			"https://dify.myyr.top/console/api/oauth/login/frappe?redirect_url=/apps"
		),
	}
	boot = _load_boot_module(monkeypatch, permission, config)
	bootinfo = {
		"app_data": [],
		"desktop_icons": [
			{"name": "I-ONE Agent", "link": "/agent"},
			{"name": "Dify", "link": "/dify"},
		],
	}

	boot.extend_bootinfo(bootinfo)
	boot.extend_bootinfo(bootinfo)
	workspace_icons = [item for item in bootinfo["desktop_icons"] if item["name"] == "I-ONE"]
	assert workspace_icons == [boot.DIFY_DESKTOP_ICON_DATA]
	assert workspace_icons[0]["link"] == "/dify"
	assert workspace_icons[0]["logo_url"] == "/assets/ione_agent/images/ione-workspace-logo.svg"
	assert workspace_icons[0]["restrict_removal"] == 1
	assert all(item["name"] != "Dify" for item in bootinfo["desktop_icons"])

	permission["allowed"] = False
	boot.extend_bootinfo(bootinfo)
	assert all(item["name"] not in {"Dify", "I-ONE"} for item in bootinfo["desktop_icons"])
	assert all(item["app_name"] != "dify_launcher" for item in bootinfo["app_data"])


def test_harness_virtual_launcher_uses_ione_agent_permission(monkeypatch):
	permission = {"allowed": False, "app_allowed": True}
	boot = _load_boot_module(monkeypatch, permission, {})
	bootinfo = {
		"app_data": [{"app_name": "ione_harness_launcher", "app_title": "old"}],
		"desktop_icons": [{"name": "IONE Harness", "link": boot.HARNESS_ROUTE}],
	}

	boot.extend_bootinfo(bootinfo)
	boot.extend_bootinfo(bootinfo)
	assert [item for item in bootinfo["app_data"] if item["app_name"] == "ione_harness_launcher"] == [
		boot.HARNESS_APP_DATA
	]
	assert [item for item in bootinfo["desktop_icons"] if item["name"] == "IONE Harness"] == [
		boot.HARNESS_DESKTOP_ICON_DATA
	]

	permission["app_allowed"] = False
	boot.extend_bootinfo(bootinfo)
	assert all(item["app_name"] != "ione_harness_launcher" for item in bootinfo["app_data"])
	assert all(item["name"] != "IONE Harness" for item in bootinfo["desktop_icons"])
