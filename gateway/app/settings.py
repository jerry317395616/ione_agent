from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _required(name: str) -> str:
	value = os.getenv(name, "").strip()
	if not value:
		raise RuntimeError(f"Required environment variable {name} is not configured")
	return value


@dataclass(frozen=True)
class Settings:
	gateway_token: str
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
	def chat_completions_url(self) -> str:
		if self.qwen_api_base.endswith("/chat/completions"):
			return self.qwen_api_base
		return f"{self.qwen_api_base}/chat/completions"
