from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any


def normalize_datetime(value: Any) -> datetime | None:
	"""Return a MySQL-safe, timezone-naive UTC datetime."""
	if value in (None, ""):
		return None

	if isinstance(value, datetime):
		parsed = value
	elif isinstance(value, date):
		parsed = datetime.combine(value, time.min)
	else:
		text = str(value).strip()
		if not text:
			return None
		if text.endswith(("Z", "z")):
			text = f"{text[:-1]}+00:00"
		try:
			parsed = datetime.fromisoformat(text)
		except (TypeError, ValueError):
			return None

	if parsed.tzinfo is not None:
		parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
	return parsed
