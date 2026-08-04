from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, convert_utc_to_system_timezone, flt, get_datetime, now_datetime

from ione_agent.gateway import GatewayClient, GatewayError

SESSION_DTYPE = "I-ONE Agent Session"
MESSAGE_DTYPE = "I-ONE Agent Message"
RUN_DTYPE = "I-ONE Agent Run"
TERMINAL_STATUSES = {"Completed", "Failed", "Stopped"}
STATUS_MAP = {
	"queued": "Queued",
	"running": "Running",
	"completed": "Completed",
	"failed": "Failed",
	"stopped": "Stopped",
}


def _require_user() -> str:
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("请先登录。"), frappe.AuthenticationError)
	return user


def _can_manage() -> bool:
	return frappe.session.user == "Administrator" or bool(
		{"System Manager", "I-ONE Agent Manager"}.intersection(frappe.get_roles())
	)


def _owned_doc(doctype: str, name: str):
	user = _require_user()
	doc = frappe.get_doc(doctype, name)
	if doc.user != user and not _can_manage():
		frappe.throw(_("你无权访问该对话。"), frappe.PermissionError)
	return doc


def _next_sequence(session: str) -> int:
	value = frappe.db.sql(
		f"select coalesce(max(sequence), 0) from `tab{MESSAGE_DTYPE}` where session=%s",
		(session,),
	)[0][0]
	return cint(value) + 1


def _new_message(
	*, session: str, user: str, role: str, content: str, run: str | None = None, message_type: str = "text"
):
	return frappe.get_doc(
		{
			"doctype": MESSAGE_DTYPE,
			"session": session,
			"user": user,
			"role": role,
			"content": content,
			"run": run,
			"message_type": message_type,
			"sequence": _next_sequence(session),
			"sent_at": now_datetime(),
			"visible": 1,
		}
	).insert(ignore_permissions=True)


def _serialize_session(row: dict[str, Any]) -> dict[str, Any]:
	return {
		"name": row.name,
		"title": row.title,
		"status": row.status,
		"last_message_at": row.last_message_at,
		"message_count": cint(row.message_count),
		"last_run": row.get("last_run"),
	}


def _serialize_message(row: dict[str, Any]) -> dict[str, Any]:
	return {
		"name": row.name,
		"role": row.role,
		"content": row.content,
		"message_type": row.message_type,
		"run": row.run,
		"sequence": cint(row.sequence),
		"sent_at": row.sent_at,
	}


def _serialize_run(doc, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
	return {
		"name": doc.name,
		"session": doc.session,
		"status": doc.status,
		"progress": flt(doc.progress),
		"current_stage": doc.current_stage,
		"gateway_run_id": doc.gateway_run_id,
		"error_message": doc.error_message,
		"started_at": doc.started_at,
		"completed_at": doc.completed_at,
		"elapsed_seconds": flt(doc.elapsed_seconds),
		"ufo_commit": doc.ufo_commit,
		"model": doc.model,
		"events": events or [],
	}


def _gateway_datetime(value: str):
	"""Convert an ISO timestamp from the gateway into Frappe's naive system time."""
	datetime_value = get_datetime(value)
	if datetime_value.tzinfo:
		datetime_value = convert_utc_to_system_timezone(datetime_value).replace(tzinfo=None)
	return datetime_value


@frappe.whitelist()
def get_bootstrap(session: str | None = None) -> dict[str, Any]:
	user = _require_user()
	filters: dict[str, Any] = {"status": ["!=", "Archived"]}
	if not _can_manage():
		filters["user"] = user

	rows = frappe.get_all(
		SESSION_DTYPE,
		filters=filters,
		fields=["name", "title", "status", "last_message_at", "message_count", "last_run"],
		order_by="last_message_at desc, creation desc",
		limit_page_length=100,
	)
	sessions = [_serialize_session(row) for row in rows]
	selected = session or (sessions[0]["name"] if sessions else None)
	messages = get_messages(selected) if selected else []

	health = {"status": "unavailable", "runtime": "UFO3", "model": "Qwen"}
	try:
		health.update(GatewayClient().health())
	except GatewayError:
		pass

	return {
		"user": user,
		"sessions": sessions,
		"selected_session": selected,
		"messages": messages,
		"gateway": health,
	}


@frappe.whitelist()
def create_session(title: str | None = None) -> dict[str, Any]:
	user = _require_user()
	doc = frappe.get_doc(
		{
			"doctype": SESSION_DTYPE,
			"title": (title or "新对话").strip()[:80],
			"user": user,
			"status": "Active",
			"last_message_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	return _serialize_session(doc)


@frappe.whitelist()
def get_messages(session: str | None = None) -> list[dict[str, Any]]:
	if not session:
		return []
	_owned_doc(SESSION_DTYPE, session)
	rows = frappe.get_all(
		MESSAGE_DTYPE,
		filters={"session": session, "visible": 1},
		fields=["name", "role", "content", "message_type", "run", "sequence", "sent_at"],
		order_by="sequence asc",
		limit_page_length=1000,
	)
	return [_serialize_message(row) for row in rows]


@frappe.whitelist()
def send_message(message: str, session: str | None = None) -> dict[str, Any]:
	user = _require_user()
	message = (message or "").strip()
	if not message:
		frappe.throw(_("请输入任务或问题。"))
	if len(message) > 12000:
		frappe.throw(_("单条消息不能超过 12000 个字符。"))

	if session:
		session_doc = _owned_doc(SESSION_DTYPE, session)
	else:
		session_doc = frappe.get_doc(
			{
				"doctype": SESSION_DTYPE,
				"title": message.replace("\n", " ")[:32],
				"user": user,
				"status": "Active",
				"last_message_at": now_datetime(),
			}
		).insert(ignore_permissions=True)

	active_run = frappe.db.exists(
		RUN_DTYPE,
		{"session": session_doc.name, "status": ["in", ["Queued", "Running"]]},
	)
	if active_run:
		frappe.throw(_("当前对话仍有任务在执行，请等待完成或先停止任务。"))

	user_message = _new_message(session=session_doc.name, user=user, role="user", content=message)
	run = frappe.get_doc(
		{
			"doctype": RUN_DTYPE,
			"session": session_doc.name,
			"user": user,
			"status": "Queued",
			"progress": 0,
			"current_stage": "等待 UFO3 接收任务",
			"request_text": message,
			"user_message": user_message.name,
		}
	).insert(ignore_permissions=True)
	user_message.db_set("run", run.name, update_modified=False)
	frappe.db.set_value(
		SESSION_DTYPE,
		session_doc.name,
		{
			"status": "Running",
			"last_message_at": now_datetime(),
			"last_run": run.name,
			"message_count": cint(session_doc.message_count) + 1,
		},
		update_modified=False,
	)

	history = get_messages(session_doc.name)[-20:]
	try:
		gateway_run = GatewayClient().start_run(
			{
				"client_run_id": run.name,
				"session_id": session_doc.gateway_session_id or session_doc.name,
				"user_id": user,
				"request": message,
				"history": [{"role": item["role"], "content": item["content"]} for item in history[:-1]],
			}
		)
	except GatewayError as exc:
		run.db_set("status", "Failed", update_modified=False)
		run.db_set("current_stage", "网关连接失败", update_modified=False)
		run.db_set("error_message", str(exc), update_modified=False)
		frappe.db.set_value(SESSION_DTYPE, session_doc.name, "status", "Failed", update_modified=False)
		_new_message(
			session=session_doc.name,
			user=user,
			role="assistant",
			content=str(exc),
			run=run.name,
			message_type="error",
		)
		frappe.db.set_value(
			SESSION_DTYPE, session_doc.name, "message_count", cint(session_doc.message_count) + 2
		)
		return {"session": session_doc.name, "run": _serialize_run(run), "accepted": False}

	run.db_set("gateway_run_id", gateway_run["run_id"], update_modified=False)
	run.db_set("status", STATUS_MAP.get(gateway_run.get("status"), "Queued"), update_modified=False)
	if gateway_run.get("session_id") and not session_doc.gateway_session_id:
		frappe.db.set_value(
			SESSION_DTYPE,
			session_doc.name,
			"gateway_session_id",
			gateway_run["session_id"],
			update_modified=False,
		)
	return {"session": session_doc.name, "run": _serialize_run(run), "accepted": True}


def _sync_run(run, payload: dict[str, Any]) -> None:
	status = STATUS_MAP.get(str(payload.get("status", "")).lower(), run.status)
	run.status = status
	run.progress = flt(payload.get("progress"))
	run.current_stage = payload.get("current_stage") or run.current_stage
	run.error_message = payload.get("error") or ""
	run.response_text = payload.get("answer") or ""
	run.ufo_commit = payload.get("ufo_commit") or run.ufo_commit
	run.model = payload.get("model") or run.model
	if payload.get("started_at"):
		run.started_at = _gateway_datetime(payload["started_at"])
	if payload.get("completed_at"):
		run.completed_at = _gateway_datetime(payload["completed_at"])
	run.elapsed_seconds = flt(payload.get("elapsed_seconds"))
	run.save(ignore_permissions=True)

	if status not in TERMINAL_STATUSES:
		return
	if not run.assistant_message:
		if status == "Completed":
			content = run.response_text or "UFO3 已完成任务，但没有返回可显示的文本结果。"
			message_type = "text"
		else:
			content = run.error_message or ("任务已停止。" if status == "Stopped" else "任务执行失败。")
			message_type = "error"
		message = _new_message(
			session=run.session,
			user=run.user,
			role="assistant",
			content=content,
			run=run.name,
			message_type=message_type,
		)
		run.db_set("assistant_message", message.name, update_modified=False)

	message_count = frappe.db.count(MESSAGE_DTYPE, {"session": run.session, "visible": 1})
	frappe.db.set_value(
		SESSION_DTYPE,
		run.session,
		{
			"status": status,
			"last_message_at": now_datetime(),
			"message_count": message_count,
		},
		update_modified=False,
	)


@frappe.whitelist()
def get_run(run: str) -> dict[str, Any]:
	doc = _owned_doc(RUN_DTYPE, run)
	events: list[dict[str, Any]] = []
	if doc.status not in TERMINAL_STATUSES and doc.gateway_run_id:
		try:
			payload = GatewayClient().get_run(doc.gateway_run_id)
			_sync_run(doc, payload)
			events = payload.get("events") or []
		except GatewayError as exc:
			return {**_serialize_run(doc), "poll_error": str(exc)}
	return _serialize_run(doc, events)


@frappe.whitelist()
def stop_run(run: str) -> dict[str, Any]:
	doc = _owned_doc(RUN_DTYPE, run)
	if doc.status in TERMINAL_STATUSES:
		return _serialize_run(doc)
	if doc.gateway_run_id:
		try:
			payload = GatewayClient().stop_run(doc.gateway_run_id)
			_sync_run(doc, payload)
		except GatewayError as exc:
			frappe.throw(str(exc))
	return _serialize_run(doc)


@frappe.whitelist()
def archive_session(session: str) -> None:
	doc = _owned_doc(SESSION_DTYPE, session)
	if doc.status == "Running":
		frappe.throw(_("请先停止正在执行的任务。"))
	doc.db_set("status", "Archived")
