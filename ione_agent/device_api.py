from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import secrets
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, get_url, now_datetime

from ione_agent.api import _can_manage, _require_user
from ione_agent.gateway import GatewayClient, GatewayError

DEVICE_DTYPE = "I-ONE Agent Device"
PAIRING_DTYPE = "I-ONE Agent Pairing"
PAIRING_MINUTES = 20
DEVICE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{8,80}$")
WINDOWS_CAPABILITIES = [
	"desktop_automation",
	"office_applications",
	"excel",
	"word",
	"powerpoint",
	"web_browsing",
]


def _digest(token: str) -> str:
	return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _get_pairing(pairing_token: str):
	pairing_token = (pairing_token or "").strip()
	if len(pairing_token) < 32:
		frappe.throw(_("配对链接无效。"), frappe.AuthenticationError)
	digest = _digest(pairing_token)
	name = frappe.db.get_value(PAIRING_DTYPE, {"token_hash": digest}, "name")
	if not name:
		frappe.throw(_("配对链接无效。"), frappe.AuthenticationError)
	doc = frappe.get_doc(PAIRING_DTYPE, name)
	if not hmac.compare_digest(doc.token_hash or "", digest):
		frappe.throw(_("配对链接无效。"), frappe.AuthenticationError)
	if get_datetime(doc.expires_at) <= now_datetime():
		if doc.status == "Pending":
			doc.db_set("status", "Expired", update_modified=False)
		frappe.throw(_("配对链接已过期，请返回 I-ONE Agent 重新生成。"), frappe.AuthenticationError)
	return doc


def _template(name: str) -> str:
	path = Path(frappe.get_app_path("ione_agent", "device", "windows", name))
	return path.read_text(encoding="utf-8")


@frappe.whitelist()
def create_pairing() -> dict[str, Any]:
	user = _require_user()
	frappe.db.set_value(
		PAIRING_DTYPE,
		{"user": user, "status": "Pending"},
		"status",
		"Expired",
		update_modified=False,
	)
	token = secrets.token_urlsafe(36)
	expires_at = add_to_date(now_datetime(), minutes=PAIRING_MINUTES)
	doc = frappe.get_doc(
		{
			"doctype": PAIRING_DTYPE,
			"user": user,
			"status": "Pending",
			"expires_at": expires_at,
			"token_hash": _digest(token),
		}
	).insert(ignore_permissions=True)
	download_url = (
		f"{get_url()}/api/method/ione_agent.device_api.download_windows_installer?"
		f"{urlencode({'pairing_token': token})}"
	)
	return {
		"pairing": doc.name,
		"expires_at": expires_at,
		"download_url": download_url,
	}


@frappe.whitelist(allow_guest=True)
def download_windows_installer(pairing_token: str):
	pairing = _get_pairing(pairing_token)
	if pairing.status not in {"Pending", "Claiming"}:
		frappe.throw(_("该配对链接已经使用，请重新生成安装包。"), frappe.AuthenticationError)
	replacements = {
		"__IONE_SITE_URL__": get_url().rstrip("/"),
		"__IONE_PAIRING_TOKEN__": pairing_token,
	}
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
		for filename in ("install.cmd", "install.ps1", "launch.ps1", "uninstall.ps1"):
			content = _template(filename)
			for source, target in replacements.items():
				content = content.replace(source, target)
			archive.writestr(filename, content.encode("utf-8-sig"))
	frappe.local.response.filename = "I-ONE-Agent-Windows.zip"
	frappe.local.response.filecontent = buffer.getvalue()
	frappe.local.response.type = "download"
	frappe.local.response.display_content_as = "attachment"


@frappe.whitelist(allow_guest=True, methods=["POST"])
def claim_pairing(
	pairing_token: str,
	device_id: str,
	device_name: str,
	client_version: str = "0.1.0",
) -> dict[str, Any]:
	pairing = _get_pairing(pairing_token)
	if pairing.status != "Pending":
		frappe.throw(_("该配对链接已经使用，请重新生成安装包。"), frappe.AuthenticationError)
	device_id = (device_id or "").strip()
	device_name = (device_name or "").strip()[:120]
	if not DEVICE_ID_PATTERN.fullmatch(device_id) or not device_name:
		frappe.throw(_("设备信息无效。"))
	pairing.db_set("status", "Claiming", update_modified=False)
	device_token = secrets.token_urlsafe(48)
	try:
		gateway_device = GatewayClient().register_device(
			{
				"device_id": device_id,
				"device_name": device_name,
				"user_id": pairing.user,
				"device_token": device_token,
				"platform": "windows",
				"client_version": client_version,
				"capabilities": WINDOWS_CAPABILITIES,
			}
		)
	except GatewayError as exc:
		frappe.throw(str(exc))

	values = {
		"device_name": device_name,
		"user": pairing.user,
		"status": "Offline",
		"platform": "Windows",
		"client_version": client_version,
		"capabilities": json.dumps(WINDOWS_CAPABILITIES, ensure_ascii=False, indent=2),
		"paired_at": now_datetime(),
		"last_seen_at": None,
		"revoked_at": None,
	}
	if frappe.db.exists(DEVICE_DTYPE, device_id):
		device = frappe.get_doc(DEVICE_DTYPE, device_id)
		if device.user != pairing.user and not _can_manage():
			frappe.throw(_("该设备已经属于其他用户。"), frappe.PermissionError)
		device.update(values)
		device.save(ignore_permissions=True)
	else:
		device = frappe.get_doc(
		{"doctype": DEVICE_DTYPE, "device_id": device_id, **values}
		).insert(ignore_permissions=True)
	pairing.db_set(
		{
			"status": "Used",
			"used_at": now_datetime(),
			"device": device.name,
		},
		update_modified=False,
	)
	return {
		"device_id": device_id,
		"device_name": device_name,
		"connection_url": gateway_device["connection_url"],
		"client_version": client_version,
	}


@frappe.whitelist()
def get_devices() -> list[dict[str, Any]]:
	user = _require_user()
	filters = {} if _can_manage() else {"user": user}
	rows = frappe.get_all(
		DEVICE_DTYPE,
		filters=filters,
		fields=[
			"name",
			"device_id",
			"device_name",
			"user",
			"status",
			"platform",
			"client_version",
			"paired_at",
			"last_seen_at",
			"revoked_at",
		],
		order_by="creation desc",
	)
	try:
		gateway_devices = {item["device_id"]: item for item in GatewayClient().list_devices()}
	except GatewayError:
		gateway_devices = {}
	for row in rows:
		gateway = gateway_devices.get(row.device_id)
		if not gateway:
			continue
		row.status = gateway["status"].title()
		row.last_seen_at = gateway.get("last_seen_at") or row.last_seen_at
	return rows


@frappe.whitelist(methods=["POST"])
def revoke_device(device_id: str) -> dict[str, Any]:
	user = _require_user()
	device = frappe.get_doc(DEVICE_DTYPE, device_id)
	if device.user != user and not _can_manage():
		frappe.throw(_("你无权管理该设备。"), frappe.PermissionError)
	try:
		GatewayClient().revoke_device(device.device_id)
	except GatewayError as exc:
		frappe.throw(str(exc))
	device.db_set(
		{"status": "Revoked", "revoked_at": now_datetime()},
		update_modified=False,
	)
	return {"device_id": device.device_id, "status": "Revoked"}
