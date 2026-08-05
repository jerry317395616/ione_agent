from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ione_agent.temporal import normalize_datetime


def test_normalize_datetime_accepts_utc_iso_timestamp():
	assert normalize_datetime("2025-11-17T15:38:00Z") == datetime(2025, 11, 17, 15, 38)


def test_normalize_datetime_converts_offset_to_naive_utc():
	assert normalize_datetime("2026-08-05T18:00:00+08:00") == datetime(2026, 8, 5, 10)
	value = datetime(2026, 8, 5, 18, tzinfo=timezone(timedelta(hours=8)))
	assert normalize_datetime(value) == datetime(2026, 8, 5, 10)


def test_normalize_datetime_accepts_date_values():
	assert normalize_datetime("2026-08-05") == datetime(2026, 8, 5)
	assert normalize_datetime(date(2026, 8, 5)) == datetime(2026, 8, 5)


def test_normalize_datetime_ignores_invalid_values():
	assert normalize_datetime(None) is None
	assert normalize_datetime("") is None
	assert normalize_datetime("未公布") is None
