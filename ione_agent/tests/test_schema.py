from __future__ import annotations

import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = APP_ROOT / "i_one_agent" / "doctype"
WINDOWS_INSTALLER = APP_ROOT / "device" / "windows" / "install.ps1"
WINDOWS_LAUNCHER = APP_ROOT / "device" / "windows" / "launch.ps1"
AGENT_TEMPLATE = APP_ROOT / "www" / "agent.html"
AGENT_SCRIPT = APP_ROOT / "public" / "js" / "agent.js"


def _schemas():
	for path in DOCTYPE_ROOT.glob("*/*.json"):
		yield path, json.loads(path.read_text(encoding="utf-8"))


def test_expected_doctypes_exist():
	names = {schema["name"] for _, schema in _schemas()}
	assert names == {
		"I-ONE Agent Session",
		"I-ONE Agent Message",
		"I-ONE Agent Run",
		"I-ONE Agent Device",
		"I-ONE Agent Pairing",
	}


def test_doctype_fields_are_unique_and_ordered():
	for path, schema in _schemas():
		fieldnames = [field["fieldname"] for field in schema["fields"]]
		assert len(fieldnames) == len(set(fieldnames)), path
		assert set(fieldnames) == set(schema["field_order"]), path
		assert schema["module"] == "I-ONE Agent", path


def test_user_scope_is_present_on_all_persistent_records():
	for path, schema in _schemas():
		fields = {field["fieldname"]: field for field in schema["fields"]}
		assert fields["user"]["options"] == "User", path
		assert fields["user"]["reqd"] == 1, path


def test_windows_installer_uses_text_download_and_explicit_acl_identity():
	installer = WINDOWS_INSTALLER.read_text(encoding="utf-8")
	assert 'Invoke-RestMethod "https://astral.sh/uv/install.ps1"' in installer
	assert 'Invoke-Expression ([string]$installer)' in installer
	assert '"$($env:USERNAME):(R,W)"' in installer
	assert '$PythonVersion = "3.10"' in installer
	assert 'Replacing incompatible Python $ExistingVersion environment' in installer
	assert 'client_version = "0.2.8"' in installer


def test_windows_launcher_trims_dpapi_ciphertext_before_decrypting():
	launcher = WINDOWS_LAUNCHER.read_text(encoding="utf-8")
	assert '(Get-Content -Raw $ConfigPath).Trim()' in launcher


def test_agent_assets_are_versioned_and_device_modal_opens_without_waiting():
	template = AGENT_TEMPLATE.read_text(encoding="utf-8")
	script = AGENT_SCRIPT.read_text(encoding="utf-8")
	assert "agent.css?v={{ asset_version }}" in template
	assert "agent.js?v={{ asset_version }}" in template
	open_modal = script.split("function openDeviceModal()", 1)[1].split(
		"function closeDeviceModal()", 1
	)[0]
	assert "els.deviceModal.hidden = false" in open_modal
	assert "await loadDevices()" not in open_modal
