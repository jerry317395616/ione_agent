from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import frappe
from frappe import _

from ione_agent.permissions import has_app_permission

TOKEN_TTL_SECONDS = 60
TOKEN_BYTES = 48
TOKEN_PREFIX = "ione_agent:sso:"
SHARED_SECRET_HEADER = "X-I-ONE-SSO-Secret"


def _cache():
	cache = frappe.cache
	return cache() if callable(cache) else cache


def _token_key(token: str) -> str:
	digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
	return f"{TOKEN_PREFIX}{digest}"


def _frontend_origin() -> str:
	configured = str(frappe.conf.get("ione_agent_frontend_url") or "").strip().rstrip("/")
	parsed = urlparse(configured)
	if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
		frappe.throw(_("I-ONE Agent 登录地址配置无效。"))
	return f"{parsed.scheme}://{parsed.netloc}"


def _shared_secret() -> str:
	secret = str(frappe.conf.get("ione_agent_sso_shared_secret") or "").strip()
	if len(secret) < 32:
		frappe.log_error("ione_agent_sso_shared_secret is missing or too short", "I-ONE Agent SSO")
		frappe.throw(_("I-ONE Agent 单点登录尚未配置。"), frappe.AuthenticationError)
	return secret


def _request_shared_secret() -> str:
	request = getattr(frappe, "request", None)
	if request is None:
		return ""
	return str(request.headers.get(SHARED_SECRET_HEADER) or "").strip()


def _issue_token(user: str) -> str:
	payload = json.dumps(
		{
			"user": user,
			"expires_at": int(time.time()) + TOKEN_TTL_SECONDS,
			"site": getattr(frappe.local, "site", ""),
		},
		separators=(",", ":"),
	)
	cache = _cache()
	for _attempt in range(3):
		token = secrets.token_urlsafe(TOKEN_BYTES)
		if cache.set(_token_key(token), payload, ex=TOKEN_TTL_SECONDS, nx=True):
			return token
	frappe.throw(_("暂时无法创建 I-ONE Agent 登录令牌，请稍后重试。"))


def _consume_token(token: str) -> dict[str, Any]:
	token = str(token or "").strip()
	if len(token) < 48 or len(token) > 160:
		frappe.throw(_("登录令牌无效或已过期。"), frappe.AuthenticationError)

	raw = _cache().getdel(_token_key(token))
	if not raw:
		frappe.throw(_("登录令牌无效或已过期。"), frappe.AuthenticationError)
	if isinstance(raw, bytes):
		raw = raw.decode("utf-8")
	try:
		payload = json.loads(raw)
	except (TypeError, ValueError):
		frappe.throw(_("登录令牌无效或已过期。"), frappe.AuthenticationError)
	if int(payload.get("expires_at") or 0) < int(time.time()):
		frappe.throw(_("登录令牌无效或已过期。"), frappe.AuthenticationError)
	return payload


@frappe.whitelist()
def create_login_url() -> str:
	user = frappe.session.user
	if user in {"", "Guest"}:
		frappe.throw(_("请先登录。"), frappe.AuthenticationError)
	if not has_app_permission(user):
		frappe.throw(_("你没有使用 I-ONE Agent 的权限。"), frappe.PermissionError)
	token = _issue_token(user)
	return f"{_frontend_origin()}/api/auth/ione?{urlencode({'token': token})}"


@frappe.whitelist(allow_guest=True, methods=["POST"])
def consume_login_token(token: str) -> dict[str, Any]:
	provided_secret = _request_shared_secret()
	if not hmac.compare_digest(provided_secret, _shared_secret()):
		frappe.throw(_("单点登录服务认证失败。"), frappe.AuthenticationError)

	payload = _consume_token(token)
	user = str(payload.get("user") or "")
	user_info = frappe.db.get_value(
		"User",
		user,
		["name", "email", "full_name", "first_name", "last_name", "username", "enabled"],
		as_dict=True,
	)
	if not user_info or not user_info.enabled or not has_app_permission(user):
		frappe.throw(_("用户已停用或无权使用 I-ONE Agent。"), frappe.PermissionError)

	full_name = (user_info.full_name or "").strip()
	if not full_name:
		full_name = " ".join(part for part in (user_info.first_name, user_info.last_name) if part).strip()
	return {
		"subject": user_info.name,
		"email": user_info.email or user_info.name,
		"name": full_name or user_info.name,
		"username": user_info.username or "",
		"roles": sorted(frappe.get_roles(user)),
	}
