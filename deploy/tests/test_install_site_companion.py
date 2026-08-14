from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from unittest.mock import patch

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


def test_existing_site_model_settings_override_host_defaults() -> None:
	base = {
		"DEEPSEEK_API_BASE": "https://api.example.com",
		"IONE_CODEX_MODEL": "remote-model",
		"IONE_CODEX_MODEL_PROVIDER": "remote",
	}
	existing = {
		"DEEPSEEK_API_BASE": "http://10.144.133.1:1234",
		"IONE_CODEX_MODEL": "qwen3.6-35b-a3b-fp8",
		"IONE_CODEX_MODEL_PROVIDER": "qwen-local",
	}

	assert installer.common_bridge_env(base, existing) == existing


def test_ensure_officecli_verifies_and_installs_pinned_binary(tmp_path: Path) -> None:
	payload = b"pinned-officecli"
	path = tmp_path / "bin" / "officecli"

	class Response(io.BytesIO):
		def __enter__(self):
			return self

		def __exit__(self, *_args):
			self.close()

	with (
		patch.object(installer.platform, "machine", return_value="x86_64"),
		patch.dict(
			installer.OFFICECLI_LINUX_ASSETS,
			{"x86_64": ("officecli-linux-x64", installer.hashlib.sha256(payload).hexdigest())},
		),
		patch.object(installer, "run", return_value="OfficeCLI 1.0.144"),
	):
		version = installer.ensure_officecli(path, downloader=lambda *_args, **_kwargs: Response(payload))

	assert version == "OfficeCLI 1.0.144"
	assert path.read_bytes() == payload


def test_ensure_officecli_falls_back_to_github(tmp_path: Path) -> None:
	payload = b"pinned-officecli"
	path = tmp_path / "bin" / "officecli"
	urls: list[str] = []

	class Response(io.BytesIO):
		def __enter__(self):
			return self

		def __exit__(self, *_args):
			self.close()

	def downloader(url: str, **_kwargs):
		urls.append(url)
		if url.startswith("https://d.officecli.ai/"):
			raise installer.urllib.error.URLError("mirror unavailable")
		return Response(payload)

	with (
		patch.object(installer.platform, "machine", return_value="x86_64"),
		patch.dict(
			installer.OFFICECLI_LINUX_ASSETS,
			{"x86_64": ("officecli-linux-x64", installer.hashlib.sha256(payload).hexdigest())},
		),
		patch.object(installer, "run", return_value="OfficeCLI 1.0.144"),
	):
		installer.ensure_officecli(path, downloader=downloader)

	assert urls == [
		"https://d.officecli.ai/releases/download/v1.0.144/officecli-linux-x64",
		"https://github.com/iOfficeAI/OfficeCLI/releases/download/v1.0.144/officecli-linux-x64",
	]
	assert path.read_bytes() == payload


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


def test_librechat_service_restarts_with_bridge() -> None:
	service = installer.librechat_service(
		"child-myyr-top",
		Path("/srv/child-myyr-top"),
		"ione-librechat-child-myyr-top",
		"ione-codex-agent-child-myyr-top",
		"zyd",
	)
	assert "Requires=docker.service ione-codex-agent-child-myyr-top.service" in service
	assert "PartOf=ione-codex-agent-child-myyr-top.service" in service


def test_librechat_config_targets_site_bridge() -> None:
	config = installer.librechat_config(18100)
	assert "http://10.144.133.1:18100/v1" in config
	assert "disabled: false" in config
	assert "disabled: true" not in config
	assert "你好，我是 I-ONE Agent" in config
