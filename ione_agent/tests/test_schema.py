from __future__ import annotations

import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = APP_ROOT / "i_one_agent" / "doctype"
WINDOWS_INSTALLER = APP_ROOT / "device" / "windows" / "install.ps1"


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
	assert 'client_version = "0.2.1"' in installer
