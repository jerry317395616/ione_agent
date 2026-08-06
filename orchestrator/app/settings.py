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
	search_http_proxy: str = ""
	agent_control_model: str = "qwen"
	max_agent_iterations: int = 12
	agent_run_budget_seconds: int = 1800
	hermes_request_timeout_seconds: int = 240
	deepseek_job_timeout_seconds: int = 900
	deepseek_breaker_failures: int = 3
	deepseek_breaker_cooldown_seconds: int = 300
	checkpoint_database_url: str = ""

	@classmethod
	def from_environment(cls) -> Settings:
		control_model = os.getenv("IONE_AGENT_CONTROL_MODEL", "qwen").strip().lower()
		if control_model not in {"qwen", "deepseek"}:
			raise RuntimeError("IONE_AGENT_CONTROL_MODEL must be qwen or deepseek")
		return cls(
			api_token=required("IONE_ORCHESTRATOR_TOKEN"),
			data_dir=Path(
				os.getenv("IONE_ORCHESTRATOR_DATA_DIR", "~/.local/share/ione-agent-orchestrator")
			).expanduser().resolve(),
			qwen_base_url=required("QWEN_API_BASE").rstrip("/"),
			qwen_api_key=required("QWEN_API_KEY"),
			qwen_model=os.getenv("QWEN_MODEL", "qwen3.6-35b-a3b-fp8").strip(),
			hermes_url=os.getenv("HERMES_API_URL", "http://127.0.0.1:8642").strip().rstrip("/"),
			hermes_api_key=os.getenv("HERMES_API_KEY", "").strip(),
			searxng_url=os.getenv("SEARXNG_URL", "http://127.0.0.1:8088").strip().rstrip("/"),
			deepseek_url=os.getenv("DEEPSEEK_REVIEW_URL", "http://127.0.0.1:9474").strip().rstrip("/"),
			deepseek_token=os.getenv("DEEPSEEK_REVIEW_TOKEN", "").strip(),
			max_concurrent_runs=max(1, min(8, int(os.getenv("IONE_MAX_CONCURRENT_RUNS", "2")))),
			search_http_proxy=os.getenv("SEARCH_HTTP_PROXY", "").strip(),
			agent_control_model=control_model,
			max_agent_iterations=max(6, min(30, int(os.getenv("IONE_AGENT_MAX_ITERATIONS", "12")))),
			agent_run_budget_seconds=max(
				300, min(7200, int(os.getenv("IONE_AGENT_RUN_BUDGET_SECONDS", "1800")))
			),
			hermes_request_timeout_seconds=max(
				60, min(600, int(os.getenv("HERMES_REQUEST_TIMEOUT_SECONDS", "240")))
			),
			deepseek_job_timeout_seconds=max(
				60, min(1800, int(os.getenv("DEEPSEEK_JOB_TIMEOUT_SECONDS", "900")))
			),
			deepseek_breaker_failures=max(
				1, min(10, int(os.getenv("DEEPSEEK_BREAKER_FAILURES", "3")))
			),
			deepseek_breaker_cooldown_seconds=max(
				30, min(3600, int(os.getenv("DEEPSEEK_BREAKER_COOLDOWN_SECONDS", "300")))
			),
			checkpoint_database_url=os.getenv("IONE_CHECKPOINT_DATABASE_URL", "").strip(),
		)

	@property
	def qwen_chat_url(self) -> str:
		base = self.qwen_base_url.removesuffix("/chat/completions").rstrip("/")
		return f"{base}/chat/completions"
