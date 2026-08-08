from __future__ import annotations

import re


PUBLIC_ENGINE_NAME = "I-ONE 智能引擎"
PUBLIC_ERROR_MESSAGE = "I-ONE Agent 暂时无法完成本次请求，请稍后重试。"


_PUBLIC_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
	(
		re.compile(r"\bcodex(?:[\s_-]+app[\s_-]+server)?\b", re.IGNORECASE),
		PUBLIC_ENGINE_NAME,
	),
	(re.compile(r"\bapp[\s_-]+server\b", re.IGNORECASE), PUBLIC_ENGINE_NAME),
	(
		re.compile(r"\bdeepseek(?:[\s/_-]+(?:v?\d+[\w.-]*|api|chat|coder|reasoner|flash|pro))*\b", re.IGNORECASE),
		"I-ONE AI",
	),
	(re.compile(r"https?://api\.deepseek\.com(?:/[^\s]*)?", re.IGNORECASE), PUBLIC_ENGINE_NAME),
	(re.compile(r"\b(?:DEEPSEEK|CODEX|IONE_CODEX)_[A-Z0-9_]+\b"), "[内部配置]"),
	(re.compile(r"/(?:opt|var/lib)/ione-codex-agent(?:/[^\s]*)?", re.IGNORECASE), "[内部路径]"),
	(re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[安全凭据]"),
	(re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE), "Bearer [安全凭据]"),
	(re.compile(r"\bmodel_provider\b", re.IGNORECASE), "智能引擎"),
)


def sanitize_public_text(value: object) -> str:
	"""Remove implementation and provider details from user-visible text."""

	text = str(value or "")
	for pattern, replacement in _PUBLIC_REPLACEMENTS:
		text = pattern.sub(replacement, text)
	return text


def public_error_message(reference: str | None = None) -> str:
	if not reference:
		return PUBLIC_ERROR_MESSAGE
	return f"{PUBLIC_ERROR_MESSAGE} 如问题持续，请联系管理员并提供参考编号 {reference}。"
