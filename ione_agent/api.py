from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, convert_utc_to_system_timezone, flt, get_datetime, now_datetime

from ione_agent.dify import DifyClient, DifyError, stable_user_id
from ione_agent.gateway import GatewayClient, GatewayError
from ione_agent.lead_service import (
	TASK_DTYPE,
	build_discovery_payload,
	create_task,
	sync_task,
)
from ione_agent.orchestrator import OrchestratorClient, OrchestratorError

SESSION_DTYPE = "I-ONE Agent Session"
MESSAGE_DTYPE = "I-ONE Agent Message"
RUN_DTYPE = "I-ONE Agent Run"
TERMINAL_STATUSES = {"Completed", "Failed", "Stopped"}
TERMINAL_DISCOVERY_STATUSES = {"已完成", "部分完成", "失败", "已停止"}
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
		"run_type": doc.run_type,
		"discovery_task": doc.discovery_task,
		"status": doc.status,
		"progress": flt(doc.progress),
		"current_stage": doc.current_stage,
		"gateway_run_id": doc.gateway_run_id,
		"dify_task_id": doc.dify_task_id,
		"dify_message_id": doc.dify_message_id,
		"dify_workflow_run_id": doc.dify_workflow_run_id,
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

	health = {"status": "unavailable", "runtime": "Dify", "model": "Qwen"}
	try:
		dify = DifyClient()
		info = dify.get_info()
		health.update(
			{
				"status": "healthy",
				"runtime": "Dify",
				"model": dify.config.model_label,
				"app": info.get("name") or "Dify App",
				"mode": info.get("mode") or "chat",
			}
		)
	except DifyError:
		pass
	try:
		health["desktop"] = GatewayClient().health()
	except GatewayError:
		health["desktop"] = {"status": "unavailable"}
	try:
		health["lead_discovery"] = OrchestratorClient().health()
	except OrchestratorError:
		health["lead_discovery"] = {"status": "unavailable"}

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


def _lead_intent_hint(message: str) -> bool:
	normalized = message.lower()
	objects = ("线索", "招标", "投标", "采购公告", "商机", "获客")
	actions = ("找", "搜", "搜索", "收集", "整理", "发现", "分析", "监测")
	return any(word in normalized for word in objects) and any(word in normalized for word in actions)


def _execution_mode(message: str) -> str:
	if _lead_intent_hint(message):
		return "lead_discovery"
	try:
		DifyClient()
		return "dify"
	except DifyError:
		pass
	try:
		return OrchestratorClient().classify(message)
	except OrchestratorError:
		return "desktop"


@frappe.whitelist()
def send_message(
	message: str,
	session: str | None = None,
	profile: str | None = None,
	execution_mode: str | None = None,
) -> dict[str, Any]:
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

	run_type = (
		execution_mode
		if execution_mode in {"dify", "desktop", "lead_discovery"}
		else _execution_mode(message)
	)
	user_message = _new_message(session=session_doc.name, user=user, role="user", content=message)
	run = frappe.get_doc(
		{
			"doctype": RUN_DTYPE,
			"session": session_doc.name,
			"user": user,
			"status": "Queued",
			"run_type": run_type,
			"progress": 0,
			"current_stage": {
				"dify": "等待 Dify 接收任务",
				"lead_discovery": "等待 AI 获客编排服务接收任务",
			}.get(run_type, "等待 UFO3 接收任务"),
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
	if run_type == "dify":
		frappe.enqueue(
			"ione_agent.api.execute_dify_run",
			queue="long",
			timeout=900,
			enqueue_after_commit=True,
			job_id=f"ione-agent-dify-{run.name}",
			run_name=run.name,
		)
		return {"session": session_doc.name, "run": _serialize_run(run), "accepted": True}

	try:
		if run_type == "lead_discovery":
			task = create_task(user=user, request=message, agent_run=run.name, profile=profile)
			run.db_set("discovery_task", task.name, update_modified=False)
			gateway_run = OrchestratorClient().start_run(build_discovery_payload(task))
			task.db_set("orchestrator_run_id", gateway_run["run_id"], update_modified=False)
		else:
			gateway_run = GatewayClient().start_run(
				{
					"client_run_id": run.name,
					"session_id": session_doc.gateway_session_id or session_doc.name,
					"user_id": user,
					"request": message,
					"history": [{"role": item["role"], "content": item["content"]} for item in history[:-1]],
				}
			)
	except (GatewayError, OrchestratorError) as exc:
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
	if run_type == "desktop" and gateway_run.get("session_id") and not session_doc.gateway_session_id:
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
	result = payload.get("result") or {}
	run.response_text = payload.get("answer") or result.get("summary") or ""
	run.ufo_commit = payload.get("ufo_commit") or run.ufo_commit
	run.model = payload.get("model") or run.model
	if payload.get("started_at"):
		run.started_at = _gateway_datetime(payload["started_at"])
	if payload.get("completed_at"):
		run.completed_at = _gateway_datetime(payload["completed_at"])
	run.elapsed_seconds = flt(payload.get("elapsed_seconds"))
	run.save(ignore_permissions=True)
	if run.run_type == "lead_discovery" and run.discovery_task:
		sync_task(run.discovery_task, payload)
		if status in TERMINAL_STATUSES:
			task_status = frappe.db.get_value(TASK_DTYPE, run.discovery_task, "status")
			run.current_stage = (
				"候选线索已写入 Frappe，部分结果待人工复核"
				if task_status == "部分完成"
				else "候选线索已写入 Frappe"
			)
			run.db_set("current_stage", run.current_stage, update_modified=False)

	if status not in TERMINAL_STATUSES:
		return
	if not run.assistant_message:
		if status == "Completed":
			fallback = {
				"lead_discovery": "AI 获客任务已完成，候选线索已写入获客池。",
				"dify": "Dify 已完成任务，但没有返回可显示的文本结果。",
			}.get(run.run_type, "UFO3 已完成任务，但没有返回可显示的文本结果。")
			content = run.response_text or fallback
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


def _dify_stage(event: dict[str, Any]) -> str | None:
	event_name = str(event.get("event") or "")
	data = event.get("data") if isinstance(event.get("data"), dict) else {}
	if event_name == "workflow_started":
		return "Dify 工作流已启动"
	if event_name == "node_started":
		title = data.get("title") or data.get("node_type") or "工作流节点"
		return f"正在执行：{title}"
	if event_name == "node_finished":
		title = data.get("title") or data.get("node_type") or "工作流节点"
		return f"已完成：{title}"
	if event_name in {"message", "agent_message"}:
		return "正在生成回复"
	if event_name in {"message_end", "workflow_finished"}:
		return "正在保存结果"
	return None


def execute_dify_run(run_name: str) -> None:
	"""Execute one Dify chat stream in a Frappe long worker."""
	run = frappe.get_doc(RUN_DTYPE, run_name)
	if run.status in TERMINAL_STATUSES:
		return
	started_at = now_datetime()
	frappe.db.set_value(
		RUN_DTYPE,
		run.name,
		{
			"status": "Running",
			"progress": 5,
			"current_stage": "正在连接 Dify",
			"started_at": started_at,
		},
		update_modified=False,
	)
	frappe.db.commit()

	try:
		client = DifyClient()
		dify_user = stable_user_id(run.user)
		session = frappe.get_doc(SESSION_DTYPE, run.session)
		conversation_id = session.dify_conversation_id or None
		answer_parts: list[str] = []
		final_answer = ""
		saw_agent_message = False
		progress = 10
		known_ids: dict[str, str] = {}
		saved_ids: dict[str, str] = {}
		last_stage = ""

		for event in client.stream_chat(
			query=run.request_text,
			user=dify_user,
			conversation_id=conversation_id,
		):
			run.reload()
			if run.status == "Stopped":
				return
			event_name = str(event.get("event") or "")
			if event.get("task_id") and not known_ids.get("dify_task_id"):
				known_ids["dify_task_id"] = str(event["task_id"])
			if event.get("message_id") and not known_ids.get("dify_message_id"):
				known_ids["dify_message_id"] = str(event["message_id"])
			if event.get("workflow_run_id") and not known_ids.get("dify_workflow_run_id"):
				known_ids["dify_workflow_run_id"] = str(event["workflow_run_id"])
			if event.get("conversation_id") and not conversation_id:
				conversation_id = str(event["conversation_id"])
				frappe.db.set_value(
					SESSION_DTYPE,
					session.name,
					"dify_conversation_id",
					conversation_id,
					update_modified=False,
				)

			answer = event.get("answer")
			if isinstance(answer, str) and answer:
				if event_name == "agent_message":
					saw_agent_message = True
					answer_parts.append(answer)
				elif event_name == "message" and saw_agent_message:
					final_answer = answer
				elif event_name == "message":
					answer_parts.append(answer)

			data = event.get("data") if isinstance(event.get("data"), dict) else {}
			if event_name == "workflow_finished" and data.get("status") == "failed":
				raise DifyError(str(data.get("error") or "Dify 工作流执行失败。"))

			stage = _dify_stage(event)
			new_ids = {key: value for key, value in known_ids.items() if saved_ids.get(key) != value}
			if stage and (stage != last_stage or new_ids):
				if event_name in {"node_started", "node_finished"}:
					progress = min(85, progress + 5)
				elif event_name in {"message", "agent_message"}:
					progress = max(progress, 70)
				elif event_name in {"message_end", "workflow_finished"}:
					progress = 95
				values: dict[str, Any] = {"current_stage": stage, "progress": progress, **new_ids}
				frappe.db.set_value(RUN_DTYPE, run.name, values, update_modified=False)
				frappe.db.commit()
				last_stage = stage
				saved_ids.update(new_ids)

		run.reload()
		if run.status == "Stopped":
			return
		response_text = final_answer or "".join(answer_parts).strip()
		completed_at = now_datetime()
		_sync_run(
			run,
			{
				"status": "completed",
				"progress": 100,
				"current_stage": "Dify 任务已完成",
				"answer": response_text,
				"model": client.config.model_label,
				"started_at": started_at.isoformat(),
				"completed_at": completed_at.isoformat(),
				"elapsed_seconds": (completed_at - started_at).total_seconds(),
			},
		)
	except DifyError as exc:
		run.reload()
		if run.status == "Stopped":
			return
		completed_at = now_datetime()
		_sync_run(
			run,
			{
				"status": "failed",
				"progress": run.progress,
				"current_stage": "Dify 执行失败",
				"error": str(exc),
				"started_at": started_at.isoformat(),
				"completed_at": completed_at.isoformat(),
				"elapsed_seconds": (completed_at - started_at).total_seconds(),
			},
		)
	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), f"I-ONE Dify run failed: {run_name}")
		run.reload()
		if run.status == "Stopped":
			return
		completed_at = now_datetime()
		_sync_run(
			run,
			{
				"status": "failed",
				"progress": run.progress,
				"current_stage": "Dify 执行失败",
				"error": f"Dify 后台任务异常：{exc}",
				"started_at": started_at.isoformat(),
				"completed_at": completed_at.isoformat(),
				"elapsed_seconds": (completed_at - started_at).total_seconds(),
			},
		)


def _run_needs_poll(doc) -> bool:
	if doc.status not in TERMINAL_STATUSES:
		return True
	if doc.run_type != "lead_discovery" or not doc.discovery_task:
		return False
	task_status = frappe.db.get_value(TASK_DTYPE, doc.discovery_task, "status")
	return task_status not in TERMINAL_DISCOVERY_STATUSES


@frappe.whitelist()
def get_run(run: str) -> dict[str, Any]:
	doc = _owned_doc(RUN_DTYPE, run)
	events: list[dict[str, Any]] = []
	if _run_needs_poll(doc) and doc.gateway_run_id:
		try:
			payload = (
				OrchestratorClient().get_run(doc.gateway_run_id)
				if doc.run_type == "lead_discovery"
				else GatewayClient().get_run(doc.gateway_run_id)
			)
			_sync_run(doc, payload)
			events = payload.get("events") or []
		except (GatewayError, OrchestratorError) as exc:
			return {**_serialize_run(doc), "poll_error": str(exc)}
	return _serialize_run(doc, events)


@frappe.whitelist()
def stop_run(run: str) -> dict[str, Any]:
	doc = _owned_doc(RUN_DTYPE, run)
	if doc.status in TERMINAL_STATUSES:
		return _serialize_run(doc)
	if doc.run_type == "dify":
		try:
			if doc.dify_task_id:
				DifyClient().stop_chat(doc.dify_task_id, stable_user_id(doc.user))
			_sync_run(
				doc,
				{
					"status": "stopped",
					"progress": doc.progress,
					"current_stage": "Dify 任务已停止",
					"completed_at": now_datetime().isoformat(),
				},
			)
		except DifyError as exc:
			frappe.throw(str(exc))
		return _serialize_run(doc)
	if doc.gateway_run_id:
		try:
			payload = (
				OrchestratorClient().stop_run(doc.gateway_run_id)
				if doc.run_type == "lead_discovery"
				else GatewayClient().stop_run(doc.gateway_run_id)
			)
			_sync_run(doc, payload)
		except (GatewayError, OrchestratorError) as exc:
			frappe.throw(str(exc))
	return _serialize_run(doc)


@frappe.whitelist()
def archive_session(session: str) -> None:
	doc = _owned_doc(SESSION_DTYPE, session)
	if doc.status == "Running":
		frappe.throw(_("请先停止正在执行的任务。"))
	doc.db_set("status", "Archived")
