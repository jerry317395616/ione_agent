from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "install_site_companion.py"
SPEC = importlib.util.spec_from_file_location("install_site_companion", MODULE_PATH)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


@pytest.mark.parametrize(
	("site", "expected"),
	[
		("child.myyr.top", "child.myyr.top"),
		("CHILD.MYYR.TOP.", "child.myyr.top"),
	],
)
def test_validate_hostname(site: str, expected: str) -> None:
	assert installer.validate_hostname(site, "site") == expected


@pytest.mark.parametrize("site", ["", "bad_name.example", "-bad.example", "bad..example"])
def test_validate_hostname_rejects_unsafe_values(site: str) -> None:
	with pytest.raises(ValueError):
		installer.validate_hostname(site, "site")


def test_site_slug_is_stable() -> None:
	assert installer.site_slug("child.myyr.top") == "child-myyr-top"


def test_env_round_trip(tmp_path: Path) -> None:
	path = tmp_path / "service.env"
	installer.atomic_write(path, installer.env_text({"TOKEN": "abc", "PORT": "1234"}), 0o600)
	assert installer.parse_env(path) == {"PORT": "1234", "TOKEN": "abc"}


def test_parse_credentials_uses_last_payload() -> None:
	output = 'setup output\n{"user":"agent@example.com","api_key":"key","api_secret":"secret"}\n'
	assert installer.parse_credentials(output) == {
		"user": "agent@example.com",
		"api_key": "key",
		"api_secret": "secret",
	}


def test_compose_has_no_global_names() -> None:
	compose = installer.librechat_compose("ione/librechat:abc123")
	assert "container_name:" not in compose
	assert "name: ione-librechat" not in compose
	assert "${LIBRECHAT_HOST_PORT}" in compose


def test_librechat_config_targets_site_bridge() -> None:
	config = installer.librechat_config(18100)
	assert "http://10.144.133.1:18100/v1" in config
	assert "你好，我是 I-ONE Agent" in config
