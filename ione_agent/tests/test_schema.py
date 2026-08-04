from __future__ import annotations

import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = APP_ROOT / "i_one_agent" / "doctype"


def _schemas():
	for path in DOCTYPE_ROOT.glob("*/*.json"):
		yield path, json.loads(path.read_text(encoding="utf-8"))


def test_expected_doctypes_exist():
	names = {schema["name"] for _, schema in _schemas()}
	assert names == {"I-ONE Agent Session", "I-ONE Agent Message", "I-ONE Agent Run"}


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
