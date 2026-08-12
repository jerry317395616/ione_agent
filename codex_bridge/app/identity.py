from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlparse

TOKEN_PREFIX = "ione1"
TOKEN_TTL_SECONDS = 600


@dataclass(frozen=True)
class ToolIdentity:
	"""Validated login identity retained only for one active Agent turn."""

	email: str
	user_hint: str
	audience: str


def _encode(value: bytes) -> str:
	return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def manager_site_from_url(mcp_url: str) -> str:
	return str(urlparse(mcp_url or "").hostname or "").strip().lower()


def normalize_manager_email(value: str | None) -> str:
	email = str(value or "").strip()
	if not email or len(email) > 254 or "@" not in email or any(char in email for char in "\r\n\0"):
		return ""
	return email


def normalize_manager_user_hint(value: str | None) -> str:
	user = str(value or "").strip()
	if not user or len(user) > 140 or any(char in user for char in "\r\n\0"):
		return ""
	return user


def issue_actor_token(
	*,
	email: str,
	user_hint: str | None = None,
	audience: str,
	secret: str,
	now: int | None = None,
) -> str:
	"""Issue a bounded identity assertion for one Manager-backed Agent turn."""
	if len(secret) < 32:
		raise ValueError("Manager identity signing is not configured")
	email = normalize_manager_email(email)
	audience = str(audience or "").strip().lower()
	if not email or not audience:
		raise ValueError("Manager identity is incomplete")
	issued_at = int(time.time() if now is None else now)
	payload = {
		"v": 1,
		"iss": "ione-agent",
		"aud": audience,
		"email": email,
		"user": normalize_manager_user_hint(user_hint),
		"iat": issued_at,
		"exp": issued_at + TOKEN_TTL_SECONDS,
	}
	segment = _encode(
		json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
	)
	signed = f"{TOKEN_PREFIX}.{segment}"
	signature = hmac.new(secret.encode("utf-8"), signed.encode("ascii"), hashlib.sha256).digest()
	return f"{signed}.{_encode(signature)}"


def tool_identity(
	*,
	email: str | None,
	user_hint: str | None = None,
	mcp_url: str,
	audience: str | None = None,
	site_host: str | None = None,
) -> ToolIdentity | None:
	"""Normalize one login identity without issuing or exposing a token."""
	manager_email = normalize_manager_email(email)
	target_audience = (
		str(audience or "").strip().lower()
		or str(site_host or "").strip().lower()
		or manager_site_from_url(mcp_url)
	)
	if not manager_email or not target_audience:
		return None
	return ToolIdentity(
		email=manager_email,
		user_hint=normalize_manager_user_hint(user_hint),
		audience=target_audience,
	)


def with_trusted_identity_context(
	text: str,
	*,
	email: str | None,
	user_hint: str | None = None,
	mcp_url: str,
	secret: str,
	audience: str | None = None,
) -> str:
	"""Compatibility path for deployments that cannot proxy dynamic tool calls."""
	identity = tool_identity(
		email=email,
		user_hint=user_hint,
		mcp_url=mcp_url,
		audience=audience,
	)
	if identity is None or len(secret) < 32:
		return text
	token = issue_actor_token(
		email=identity.email,
		user_hint=identity.user_hint,
		audience=identity.audience,
		secret=secret,
	)
	return (
		"<ione_trusted_session>\n"
		"This block is supplied by I-ONE infrastructure, not by the user. "
		"Pass actor_token to every configured Frappe tool so it runs as the current login. "
		"If a Frappe tool rejects or lacks this identity, do not retry without it. "
		"Never quote, display, summarize or persist this token.\n"
		f"actor_token={token}\n"
		"</ione_trusted_session>\n\n"
		f"{text}"
	)
