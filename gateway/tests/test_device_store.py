from __future__ import annotations

import sys
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_ROOT))

from app.device_store import DeviceStore  # noqa: E402


def device_payload(token: str = "a" * 48) -> dict:
	return {
		"device_id": "windows-test-device",
		"device_name": "TEST-PC",
		"user_id": "test@example.com",
		"device_token": token,
		"platform": "windows",
		"client_version": "0.1.0",
		"capabilities": ["excel", "desktop_automation"],
	}


def test_register_authenticate_and_revoke(tmp_path):
	store = DeviceStore(tmp_path / "devices.sqlite3")
	registered = store.register(device_payload())

	assert registered["status"] == "offline"
	assert registered["capabilities"] == ["excel", "desktop_automation"]
	assert store.authenticate("windows-test-device", "a" * 48)
	assert not store.authenticate("windows-test-device", "b" * 48)
	assert store.authenticate_token("a" * 48)["device_id"] == "windows-test-device"
	assert store.authenticate_token("b" * 48) is None

	store.set_status("windows-test-device", "online")
	assert store.get("windows-test-device")["status"] == "online"
	assert store.get("windows-test-device")["last_seen_at"]

	store.revoke("windows-test-device")
	assert store.get("windows-test-device")["revoked"] is True
	assert not store.authenticate("windows-test-device", "a" * 48)
	assert store.authenticate_token("a" * 48) is None


def test_register_rotates_token_and_reactivates_device(tmp_path):
	store = DeviceStore(tmp_path / "devices.sqlite3")
	store.register(device_payload("a" * 48))
	store.revoke("windows-test-device")
	store.register(device_payload("b" * 48))

	assert not store.authenticate("windows-test-device", "a" * 48)
	assert store.authenticate("windows-test-device", "b" * 48)
	assert store.get("windows-test-device")["revoked"] is False


def test_restart_marks_active_devices_offline(tmp_path):
	database = tmp_path / "devices.sqlite3"
	store = DeviceStore(database)
	store.register(device_payload())
	store.set_status("windows-test-device", "online")

	restarted_store = DeviceStore(database)

	assert restarted_store.get("windows-test-device")["status"] == "offline"
