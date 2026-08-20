from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote, urlsplit

OAUTH_LOGIN_PATH = re.compile(r"^/console/api/oauth/login/frappe/?$")


class DifyLauncherConfigError(ValueError):
	pass


def _reject_unsafe_characters(value: str, label: str) -> None:
	if "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
		raise DifyLauncherConfigError(f"{label} contains unsafe characters")


def _origin_key(value: str) -> tuple[str, int]:
	value = str(value or "").strip()
	_reject_unsafe_characters(value, "Dify origin")
	parts = urlsplit(value)
	try:
		port = parts.port
	except ValueError as exc:
		raise DifyLauncherConfigError("Dify origin has an invalid port") from exc
	if (
		parts.scheme.lower() != "https"
		or not parts.hostname
		or parts.username
		or parts.password
		or parts.path not in {"", "/"}
		or parts.query
		or parts.fragment
	):
		raise DifyLauncherConfigError("Dify origin must be an HTTPS origin without a path")
	return parts.hostname.lower(), port or 443


def _validate_local_redirect(value: str) -> None:
	value = unquote(str(value or ""))
	_reject_unsafe_characters(value, "Dify post-login redirect")
	parts = urlsplit(value)
	segments = parts.path.split("/")
	if (
		not parts.path.startswith("/")
		or parts.path.startswith("//")
		or parts.scheme
		or parts.netloc
		or parts.query
		or parts.fragment
		or any(segment in {".", ".."} for segment in segments)
	):
		raise DifyLauncherConfigError("Dify post-login redirect must be a local path")


def validate_dify_oauth_login_url(login_url: str, allowed_origin: str) -> str:
	"""Validate and return the configured Dify OAuth entry point.

	The launcher accepts no request-provided target. The URL must point to Dify's
	OAuth-login endpoint on the separately configured HTTPS origin, and the only
	optional query argument is a local post-login path.
	"""

	login_url = str(login_url or "").strip()
	_reject_unsafe_characters(login_url, "Dify OAuth login URL")
	allowed_origin_key = _origin_key(allowed_origin)
	parts = urlsplit(login_url)
	try:
		login_port = parts.port
	except ValueError as exc:
		raise DifyLauncherConfigError("Dify OAuth login URL has an invalid port") from exc
	if (
		parts.scheme.lower() != "https"
		or not parts.hostname
		or parts.username
		or parts.password
		or parts.fragment
		or not OAUTH_LOGIN_PATH.fullmatch(unquote(parts.path))
	):
		raise DifyLauncherConfigError("Dify OAuth login URL is invalid")
	if (parts.hostname.lower(), login_port or 443) != allowed_origin_key:
		raise DifyLauncherConfigError("Dify OAuth login URL is outside the allowed origin")

	query = parse_qsl(parts.query, keep_blank_values=True)
	if len(query) > 1 or any(key != "redirect_url" for key, _value in query):
		raise DifyLauncherConfigError("Dify OAuth login URL has unsupported query arguments")
	if query:
		_validate_local_redirect(query[0][1])
	return login_url
