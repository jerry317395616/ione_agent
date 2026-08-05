from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def required(name: str) -> str:
	value = os.getenv(name, "").strip()
	if not value:
		raise RuntimeError(f"Required environment variable {name} is not configured")
	return value


@dataclass(frozen=True)
class Settings:
	api_token: str
	data_dir: Path
	qwen_base_url: str
	qwen_api_key: str
	qwen_model: str
	hermes_url: str
	hermes_api_key: str
	searxng_url: str
	deepseek_url: str
	deepseek_token: str
	max_concurrent_runs: int

	@classmethod
	def from_environment(cls) -> Settings:
		return cls(
			api_token=required("IONE_ORCHESTRATOR_TOKEN"),
			data_dir=Path(os.getenv("IONE_ORCHESTRATOR_DATA_DIR", "/var/lib/ione-agent-orchestrator")).resolve(),
			qwen_base_url=required("QWEN_API_BASE").rstrip("/"),
			qwen_api_key=required("QWEN_API_KEY"),
			qwen_model=os.getenv("QWEN_MODEL", "qwen3.6-35b-a3b-fp8").strip(),
			hermes_url=os.getenv("HERMES_API_URL", "http://127.0.0.1:8642").strip().rstrip("/"),
			hermes_api_key=os.getenv("HERMES_API_KEY", "").strip(),
			searxng_url=os.getenv("SEARXNG_URL", "http://127.0.0.1:8088").strip().rstrip("/"),
			deepseek_url=os.getenv("DEEPSEEK_REVIEW_URL", "http://127.0.0.1:9474").strip().rstrip("/"),
			deepseek_token=os.getenv("DEEPSEEK_REVIEW_TOKEN", "").strip(),
			max_concurrent_runs=max(1, min(8, int(os.getenv("IONE_MAX_CONCURRENT_RUNS", "2")))),
		)

	@property
	def qwen_chat_url(self) -> str:
		base = self.qwen_base_url.removesuffix("/chat/completions").rstrip("/")
		return f"{base}/chat/completions"
