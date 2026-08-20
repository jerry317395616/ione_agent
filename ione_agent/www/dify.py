from __future__ import annotations

from urllib.parse import urlencode

import frappe
from frappe import _

from ione_agent.dify_launcher import DifyLauncherConfigError, validate_dify_oauth_login_url
from ione_agent.permissions import has_dify_permission

no_cache = 1


def _configured_login_url() -> str:
	try:
		return validate_dify_oauth_login_url(
			frappe.conf.get("ione_agent_dify_oauth_login_url"),
			frappe.conf.get("ione_agent_dify_origin"),
		)
	except DifyLauncherConfigError as exc:
		frappe.log_error(str(exc), "I-ONE workspace launcher configuration")
		frappe.throw(_("I-ONE 单点登录入口尚未正确配置，请联系系统管理员。"))


def get_context(context):
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.local.flags.redirect_location = f"/login?{urlencode({'redirect-to': '/dify'})}"
		raise frappe.Redirect
	if not has_dify_permission(user):
		frappe.throw(_("你没有进入 I-ONE 工作台的权限。"), frappe.PermissionError)

	frappe.local.flags.redirect_location = _configured_login_url()
	raise frappe.Redirect
