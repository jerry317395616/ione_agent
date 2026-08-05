from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _required(name: str) -> str:
	value = os.getenv(name, "").strip()
	if not value:
		raise RuntimeError(f"Required environment variable {name} is not configured")
	return value


@dataclass(frozen=True)
class Settings:
	gateway_token: str
	device_server_api_key: str
	device_public_ws_url: str
	device_server_host: str
	device_server_port: int
	qwen_api_base: str
	qwen_api_key: str
	qwen_model: str
	ufo_root: Path
	data_dir: Path
	max_rounds: int
	max_step: int
	devices: dict

	@classmethod
	def from_environment(cls) -> Settings:
		devices_raw = os.getenv("UFO_DEVICES_JSON", '{"devices": []}')
		try:
			devices = json.loads(devices_raw)
		except json.JSONDecodeError as exc:
			raise RuntimeError("UFO_DEVICES_JSON must contain valid JSON") from exc
		if not isinstance(devices, dict) or not isinstance(devices.get("devices", []), list):
			raise RuntimeError("UFO_DEVICES_JSON must be an object with a devices array")
		return cls(
			gateway_token=_required("IONE_GATEWAY_TOKEN"),
			device_server_api_key=_required("IONE_DEVICE_SERVER_API_KEY"),
			device_public_ws_url=_required("IONE_DEVICE_PUBLIC_WS_URL"),
			device_server_host=os.getenv("IONE_DEVICE_SERVER_HOST", "127.0.0.1").strip(),
			device_server_port=max(1024, min(65535, int(os.getenv("IONE_DEVICE_SERVER_PORT", "5000")))),
			qwen_api_base=_required("QWEN_API_BASE").rstrip("/"),
			qwen_api_key=_required("QWEN_API_KEY"),
			qwen_model=os.getenv("QWEN_MODEL", "qwen3.6-35b-a3b-fp8").strip(),
			ufo_root=Path(os.getenv("UFO_ROOT", "/opt/UFO")).resolve(),
			data_dir=Path(os.getenv("IONE_GATEWAY_DATA_DIR", "/data")).resolve(),
			max_rounds=max(1, min(40, int(os.getenv("UFO_MAX_ROUNDS", "10")))),
			max_step=max(1, min(80, int(os.getenv("UFO_MAX_STEP", "15")))),
			devices=devices,
		)

	@property
	def openai_base_url(self) -> str:
		return self.qwen_api_base.removesuffix("/chat/completions").rstrip("/")

	@property
	def internal_device_ws_url(self) -> str:
		return (
			f"ws://{self.device_server_host}:{self.device_server_port}/ws"
			f"?token={self.device_server_api_key}"
		)

	@property
	def device_model_api_base(self) -> str:
		parsed = urlsplit(self.device_public_ws_url)
		scheme = "https" if parsed.scheme == "wss" else "http"
		return urlunsplit((scheme, parsed.netloc, "/device/openai/v1", "", ""))
