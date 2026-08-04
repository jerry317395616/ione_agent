from __future__ import annotations

import frappe

from ione_agent.permissions import has_app_permission

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/agent"
		raise frappe.Redirect
	if not has_app_permission():
		frappe.throw("你没有使用 I-ONE Agent 的权限。", frappe.PermissionError)

	context.no_cache = 1
	context.title = "I-ONE Agent"
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.user = frappe.session.user
	return context
