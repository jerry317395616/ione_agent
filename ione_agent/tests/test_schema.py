from __future__ import annotations

import json
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = APP_ROOT / "i_one_agent" / "doctype"
WINDOWS_INSTALLER = APP_ROOT / "device" / "windows" / "install.ps1"
WINDOWS_LAUNCHER = APP_ROOT / "device" / "windows" / "launch.ps1"
AGENT_TEMPLATE = APP_ROOT / "www" / "agent.html"
AGENT_SCRIPT = APP_ROOT / "public" / "js" / "agent.js"
AGENT_API = APP_ROOT / "api.py"
DIFY_CLIENT = APP_ROOT / "dify.py"
DIFY_PAGE = APP_ROOT / "www" / "dify.py"
DIFY_LOGO = APP_ROOT / "public" / "images" / "dify-logo.svg"
HOOKS = APP_ROOT / "hooks.py"
LEAD_SERVICE = APP_ROOT / "lead_service.py"


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
		"I-ONE Lead Discovery Profile",
		"I-ONE Lead Source",
		"I-ONE Lead Discovery Task",
		"I-ONE Lead Candidate",
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
	assert 'client_version = "0.2.14"' in installer
	assert 'timeout: float = 20,' in installer
	assert "Set-UfoWindowFocusFallback" in installer
	assert 'win32gui.SetForegroundWindow(handle)' in installer
	assert (
		'& $Git -C $UfoRoot checkout -- ufo/client/websocket.py '
		'ufo/client/mcp/local_servers/ui_mcp_server.py'
	) in installer


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


def test_completed_lead_runs_can_resume_an_incomplete_frappe_sync():
	api = AGENT_API.read_text(encoding="utf-8")
	assert "def _run_needs_poll(doc)" in api
	assert "task_status not in TERMINAL_DISCOVERY_STATUSES" in api
	assert "if _run_needs_poll(doc) and doc.gateway_run_id:" in api


def test_dify_conversations_and_runs_are_auditable():
	session_path = DOCTYPE_ROOT / "i_one_agent_session" / "i_one_agent_session.json"
	run_path = DOCTYPE_ROOT / "i_one_agent_run" / "i_one_agent_run.json"
	session = json.loads(session_path.read_text(encoding="utf-8"))
	run = json.loads(run_path.read_text(encoding="utf-8"))
	session_fields = {field["fieldname"]: field for field in session["fields"]}
	run_fields = {field["fieldname"]: field for field in run["fields"]}
	assert session_fields["dify_conversation_id"]["read_only"] == 1
	assert session_fields["dify_conversation_id"]["unique"] == 1
	assert "dify" in run_fields["run_type"]["options"].splitlines()
	assert run_fields["run_type"]["default"] == "desktop"
	for fieldname in ("dify_task_id", "dify_message_id", "dify_workflow_run_id"):
		assert run_fields[fieldname]["read_only"] == 1


def test_dify_api_key_stays_in_the_frappe_backend():
	client = DIFY_CLIENT.read_text(encoding="utf-8")
	api = AGENT_API.read_text(encoding="utf-8")
	assert 'frappe.conf.get("ione_agent_dify_api_key")' in client
	assert 'f"Bearer {self.config.api_key}"' in client
	assert 'f"{self.config.base_url}/chat-messages"' in client
	assert "hmac.new(" in client
	assert "def execute_dify_run(run_name: str)" in api
	assert 'if doc.run_type == "dify":' in api


def test_new_agent_runs_do_not_route_to_dify():
	api = AGENT_API.read_text(encoding="utf-8")
	execution_mode = api.split("def _execution_mode(message: str)", 1)[1].split(
		"@frappe.whitelist()", 1
	)[0]
	assert "DifyClient" not in execution_mode
	assert 'requested not in {"desktop", "lead_discovery"}' in execution_mode
	assert 'run_type == "dify"' not in api.split("def send_message(", 1)[1].split(
		"def _sync_run", 1
	)[0]
	assert 'if doc.run_type == "dify":' in api


def test_dify_page_requires_frappe_role_gate_and_redirects_without_a_token():
	page = DIFY_PAGE.read_text(encoding="utf-8")
	assert 'user == "Guest"' in page
	assert "has_dify_permission(user)" in page
	assert 'frappe.conf.get("ione_agent_dify_oauth_login_url")' in page
	assert "redirect_location = _configured_login_url()" in page
	assert "token=" not in page


def test_dify_launcher_uses_bootinfo_without_declaring_a_second_installed_app():
	hooks = HOOKS.read_text(encoding="utf-8")
	assert 'extend_bootinfo = ["ione_agent.boot.extend_bootinfo"]' in hooks
	assert hooks.count('"name": app_name') == 1
	assert "dify_launcher" not in hooks.split("add_to_apps_screen =", 1)[1].split(
		"extend_bootinfo =", 1
	)[0]
	assert DIFY_LOGO.is_file()


def test_crm_conversion_skips_missing_link_master_data():
	service = LEAD_SERVICE.read_text(encoding="utf-8")
	assert 'field.fieldtype == "Link"' in service
	assert "frappe.db.exists(field.options, value)" in service
	assert "_set_crm_field(lead, meta, fieldname, value)" in service
	assert "_refresh_task_crm_count(candidate)" in service
	assert '"crm_lead": ["is", "set"]' in service


def test_candidate_source_url_supports_long_public_links():
	schema_path = DOCTYPE_ROOT / "i_one_lead_candidate" / "i_one_lead_candidate.json"
	schema = json.loads(schema_path.read_text(encoding="utf-8"))
	fields = {field["fieldname"]: field for field in schema["fields"]}
	assert fields["source_url"]["fieldtype"] == "Small Text"
	service = LEAD_SERVICE.read_text(encoding="utf-8")
	assert "def _data_value" in service
	assert '"source_url": item.get("source_url")' in service


def test_new_lead_tasks_use_v2_planning_status():
	task_schema_path = DOCTYPE_ROOT / "i_one_lead_discovery_task" / "i_one_lead_discovery_task.json"
	task_schema = json.loads(task_schema_path.read_text(encoding="utf-8"))
	fields = {field["fieldname"]: field for field in task_schema["fields"]}
	assert "正在规划" in fields["status"]["options"]
	service = LEAD_SERVICE.read_text(encoding="utf-8")
	assert '"planning": "正在规划"' in service
	assert '"graph_version": "lead-agent-v2"' in service


def test_terminal_lead_run_reports_frappe_sync_completion():
	api = AGENT_API.read_text(encoding="utf-8")
	assert "候选线索已写入 Frappe，部分结果待人工复核" in api
	assert 'run.db_set("current_stage", run.current_stage' in api
