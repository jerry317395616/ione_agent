#!/usr/bin/env python3
"""Install I-ONE Agent and a site-isolated LibreChat companion stack.

Run this script as root on the Frappe host. It deliberately provisions Docker
and systemd from the host rather than exposing the Docker socket to Frappe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SITE_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
OFFICECLI_VERSION = "v1.0.144"
OFFICECLI_LINUX_ASSETS = {
	"x86_64": (
		"officecli-linux-x64",
		"32ef7a21a54a4ca6c9806bf5e9f3d32bfb1291017329c55044cb2aac71822eb8",
	),
	"aarch64": (
		"officecli-linux-arm64",
		"56ec2c3114b66f6490888b6778cbb8413a65911a26cacc7207f29e13424966da",
	),
}

COMMON_BRIDGE_ENV = {
	"DEEPSEEK_API_BASE",
	"DEEPSEEK_API_KEY",
	"IONE_CODEX_BIN",
	"IONE_CODEX_MODEL",
	"IONE_CODEX_MODEL_CONTEXT_WINDOW",
	"IONE_CODEX_MODEL_PROVIDER",
	"IONE_CODEX_NETWORK_ACCESS",
	"IONE_CODEX_SANDBOX",
	"IONE_CODEX_WORKSPACE_SCOPE",
	"IONE_CODEX_RPC_TIMEOUT_SECONDS",
	"IONE_CODEX_MESSAGE_LIMIT_BYTES",
	"IONE_CODEX_KEEPALIVE_SECONDS",
	"IONE_OFFICECLI_BIN",
	"IONE_ORACLE_BROWSER_ENABLED",
	"IONE_ORACLE_BROWSER_MAX_TOOL_ROUNDS",
	"IONE_ORACLE_BROWSER_TIMEOUT_SECONDS",
	"IONE_ORACLE_BROWSER_TOKEN",
	"IONE_ORACLE_BROWSER_URL",
}


def run(command: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
	completed = subprocess.run(
		command,
		cwd=cwd,
		check=True,
		text=True,
		stdout=subprocess.PIPE if capture else None,
		stderr=subprocess.PIPE if capture else None,
	)
	return completed.stdout.strip() if capture else ""


def validate_hostname(value: str, label: str) -> str:
	value = value.strip().lower().rstrip(".")
	if not SITE_PATTERN.fullmatch(value) or ".." in value:
		raise ValueError(f"Invalid {label}: {value!r}")
	return value


def site_slug(site: str) -> str:
	slug = SLUG_PATTERN.sub("-", site.lower()).strip("-")
	if not slug:
		raise ValueError("The site name does not produce a usable instance slug")
	return slug[:63]


def validate_port(value: int, label: str) -> int:
	if not 1024 <= value <= 65535:
		raise ValueError(f"{label} must be between 1024 and 65535")
	return value


def parse_env(path: Path) -> dict[str, str]:
	values: dict[str, str] = {}
	for raw_line in path.read_text(encoding="utf-8").splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		name, value = line.split("=", 1)
		if ENV_NAME_PATTERN.fullmatch(name):
			values[name] = value
	return values


def env_text(values: dict[str, str]) -> str:
	lines: list[str] = []
	for name in sorted(values):
		value = values[name]
		if "\n" in value or "\r" in value:
			raise ValueError(f"Environment value for {name} contains a newline")
		lines.append(f"{name}={value}")
	return "\n".join(lines) + "\n"


def common_bridge_env(base: dict[str, str], existing: dict[str, str]) -> dict[str, str]:
	"""Keep a site's explicit model settings when an installation is repeated."""

	values: dict[str, str] = {}
	for name in COMMON_BRIDGE_ENV:
		value = existing.get(name) or base.get(name)
		if value:
			values[name] = value
	return values


def atomic_write(path: Path, content: str, mode: int) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
	temporary.write_text(content, encoding="utf-8", newline="\n")
	os.chmod(temporary, mode)
	os.replace(temporary, path)


def ensure_officecli(
	path: Path,
	*,
	version: str = OFFICECLI_VERSION,
	downloader=urllib.request.urlopen,
) -> str:
	"""Install a pinned OfficeCLI release atomically after SHA-256 verification."""
	machine = platform.machine().lower()
	if machine == "amd64":
		machine = "x86_64"
	if machine == "arm64":
		machine = "aarch64"
	if machine not in OFFICECLI_LINUX_ASSETS:
		raise RuntimeError(f"OfficeCLI does not support this server architecture: {machine}")
	asset, expected_sha256 = OFFICECLI_LINUX_ASSETS[machine]
	if path.is_file():
		try:
			installed = run([str(path), "--version"], capture=True)
			if version.lstrip("v") in installed:
				return installed
		except (OSError, subprocess.CalledProcessError):
			pass
	urls = (
		f"https://d.officecli.ai/releases/download/{version}/{asset}",
		f"https://github.com/iOfficeAI/OfficeCLI/releases/download/{version}/{asset}",
	)
	payload = b""
	last_error: Exception | None = None
	for url in urls:
		try:
			with downloader(url, timeout=120) as response:
				payload = response.read()
			break
		except (OSError, urllib.error.URLError) as exc:
			last_error = exc
	if not payload:
		raise RuntimeError("Unable to download the pinned OfficeCLI release") from last_error
	actual_sha256 = hashlib.sha256(payload).hexdigest()
	if actual_sha256 != expected_sha256:
		raise RuntimeError("OfficeCLI SHA-256 verification failed")
	path.parent.mkdir(parents=True, exist_ok=True)
	temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
	temporary.write_bytes(payload)
	os.chmod(temporary, 0o755)
	os.replace(temporary, path)
	return run([str(path), "--version"], capture=True)


def ensure_repo(path: Path, repository: str, branch: str, owner: str) -> str:
	path.parent.mkdir(parents=True, exist_ok=True)
	if not (path / ".git").is_dir():
		run(["sudo", "-u", owner, "git", "clone", "--branch", branch, repository, str(path)])
	else:
		run(["sudo", "-u", owner, "git", "-C", str(path), "fetch", "--prune", "origin"])
		run(["sudo", "-u", owner, "git", "-C", str(path), "checkout", branch])
		run(["sudo", "-u", owner, "git", "-C", str(path), "merge", "--ff-only", f"origin/{branch}"])
	return run(["git", "-C", str(path), "rev-parse", "--short=12", "HEAD"], capture=True)


def compose_command(bench: Path, *arguments: str, capture: bool = False) -> str:
	return run(
		["docker", "compose", "-f", str(bench / "compose.yaml"), *arguments], cwd=bench, capture=capture
	)


def installed_apps(bench: Path, site: str) -> set[str]:
	output = compose_command(
		bench,
		"exec",
		"-T",
		"backend",
		"bench",
		"--site",
		site,
		"list-apps",
		capture=True,
	)
	return {line.split()[0] for line in output.splitlines() if line.strip()}


def bench(bench_path: Path, site: str, *arguments: str, capture: bool = False) -> str:
	return compose_command(
		bench_path,
		"exec",
		"-T",
		"backend",
		"bench",
		"--site",
		site,
		*arguments,
		capture=capture,
	)


def parse_credentials(output: str) -> dict[str, str]:
	for line in reversed(output.splitlines()):
		line = line.strip()
		if line.startswith("{") and line.endswith("}"):
			payload = json.loads(line)
			if all(payload.get(key) for key in ("user", "api_key", "api_secret")):
				return {key: str(payload[key]) for key in ("user", "api_key", "api_secret")}
	raise RuntimeError("Frappe did not return integration credentials")


def wait_for_url(url: str, timeout: int = 180) -> None:
	deadline = time.monotonic() + timeout
	last_error = ""
	while time.monotonic() < deadline:
		try:
			with urllib.request.urlopen(url, timeout=5) as response:
				if 200 <= response.status < 300:
					return
		except (OSError, urllib.error.URLError) as exc:
			last_error = str(exc)
		time.sleep(3)
	raise RuntimeError(f"Health check timed out for {url}: {last_error}")


def librechat_compose(image: str) -> str:
	return f"""services:
  api:
    image: {image}
    restart: unless-stopped
    env_file:
      - .env.ione
    environment:
      HOST: 0.0.0.0
      PORT: 3080
      MONGO_URI: mongodb://mongodb:27017/LibreChat
      MEILI_HOST: http://meilisearch:7700
      CONFIG_PATH: /app/librechat.yaml
      NO_PROXY: localhost,127.0.0.1,mongodb,meilisearch,host.docker.internal
    extra_hosts:
      - host.docker.internal:host-gateway
    ports:
      - ${{LIBRECHAT_BIND_IP}}:${{LIBRECHAT_HOST_PORT}}:3080
    volumes:
      - ./.env.ione:/app/.env:ro
      - ./librechat.yaml:/app/librechat.yaml:ro
      - ./runtime/images:/app/client/public/images
      - ./runtime/uploads:/app/uploads
      - ./runtime/logs:/app/logs
    depends_on:
      mongodb:
        condition: service_healthy
      meilisearch:
        condition: service_healthy
    healthcheck:
      test: [CMD, node, -e, "fetch('http://127.0.0.1:3080/health').then(r=>{{if(!r.ok)process.exit(1)}}).catch(()=>process.exit(1))"]
      interval: 15s
      timeout: 5s
      retries: 12
      start_period: 90s
    mem_limit: 2g
    cpus: 4
    logging:
      driver: json-file
      options:
        max-size: 20m
        max-file: "5"

  mongodb:
    image: mongo:8.0.20
    restart: unless-stopped
    command: mongod --noauth --wiredTigerCacheSizeGB 0.25
    volumes:
      - ./runtime/mongodb:/data/db
    healthcheck:
      test: [CMD, mongosh, --quiet, --eval, "db.adminCommand('ping').ok"]
      interval: 10s
      timeout: 5s
      retries: 20
      start_period: 30s
    mem_limit: 768m
    cpus: 2
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"

  meilisearch:
    image: getmeili/meilisearch:v1.35.1
    restart: unless-stopped
    env_file:
      - .env.ione
    environment:
      MEILI_NO_ANALYTICS: "true"
    volumes:
      - ./runtime/meilisearch:/meili_data
    healthcheck:
      test: [CMD, wget, --no-verbose, --spider, http://127.0.0.1:7700/health]
      interval: 10s
      timeout: 5s
      retries: 20
      start_period: 20s
    mem_limit: 512m
    cpus: 2
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
"""


def librechat_config(bridge_port: int) -> str:
	return f"""version: 1.3.13
cache: true

interface:
  customWelcome: '你好，我是 I-ONE Agent。请直接告诉我你想了解或完成什么。'
  modelSelect: false
  parameters: false
  presets: false
  prompts:
    use: false
    create: false
    share: false
    public: false
  bookmarks: true
  multiConvo: true
  agents:
    use: false
    create: false
    share: false
    public: false
  marketplace:
    use: false
  fileSearch: false
  fileCitations: true

registration:
  socialLogins: []

endpoints:
  custom:
    - name: 'I-ONE Agent'
      apiKey: '${{IONE_AGENT_API_TOKEN}}'
      baseURL: 'http://10.144.133.1:{bridge_port}/v1'
      headers:
        X-LibreChat-User-Id: '{{{{LIBRECHAT_USER_ID}}}}'
        X-LibreChat-Conversation-Id: '{{{{LIBRECHAT_BODY_CONVERSATIONID}}}}'
        X-I-ONE-Manager-User-Email: '{{{{LIBRECHAT_USER_EMAIL}}}}'
        X-I-ONE-Manager-User-Name: '{{{{LIBRECHAT_USER_NAME}}}}'
        X-I-ONE-Manager-Username: '{{{{LIBRECHAT_USER_USERNAME}}}}'
      models:
        default: ['ione-agent']
        fetch: true
      customParams:
        reasoningKey: 'reasoning_content'
      titleConvo: false
      summarize: false
      modelDisplayLabel: 'I-ONE Agent'
      dropParams: ['stop', 'frequency_penalty', 'presence_penalty']

modelSpecs:
  enforce: true
  prioritize: true
  list:
    - name: 'ione-agent'
      label: 'I-ONE Agent'
      description: '面向企业业务查询、分析与执行的统一智能助手'
      default: true
      preset:
        endpoint: 'I-ONE Agent'
        model: 'ione-agent'

fileConfig:
  endpoints:
    default:
      disabled: false
"""


def librechat_service(slug: str, instance: Path, project: str, bridge_service: str, user: str) -> str:
	return f"""[Unit]
Description=I-ONE LibreChat companion for {slug}
After=docker.service network-online.target {bridge_service}.service
Requires=docker.service {bridge_service}.service
PartOf={bridge_service}.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User={user}
Group={user}
WorkingDirectory={instance}
ExecStart=/usr/bin/docker compose --project-name {project} --env-file {instance}/.env.ione -f {instance}/compose.yaml up -d --remove-orphans
ExecStop=/usr/bin/docker compose --project-name {project} --env-file {instance}/.env.ione -f {instance}/compose.yaml down
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
"""


def bridge_service(slug: str, source: Path, env_path: Path, data_root: Path, port: int) -> str:
	return f"""[Unit]
Description=I-ONE Agent site bridge for {slug}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ioneagent
Group=ioneagent
WorkingDirectory={source}/codex_bridge
Environment=HOME={data_root}
EnvironmentFile={env_path}
ExecStart=/opt/ione-codex-agent/venv/bin/uvicorn app.main:app --host 10.144.133.1 --port {port} --workers 1
Restart=always
RestartSec=5
KillMode=control-group
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths={data_root}
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def set_site_config(bench_path: Path, site: str, values: dict[str, str]) -> None:
	for key, value in values.items():
		bench(bench_path, site, "set-config", key, value)


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--site", required=True)
	parser.add_argument("--frontend-domain", required=True)
	parser.add_argument("--librechat-port", required=True, type=int)
	parser.add_argument("--bridge-port", required=True, type=int)
	parser.add_argument("--bind-ip", default="10.144.133.1")
	parser.add_argument("--bench", type=Path, default=Path("/home/zyd/frappe-direct"))
	parser.add_argument(
		"--librechat-source", type=Path, default=Path("/home/zyd/services/ione-librechat/source")
	)
	parser.add_argument("--librechat-repository", default="git@github.com:jerry317395616/LibreChat.git")
	parser.add_argument("--librechat-branch", default="main")
	parser.add_argument("--ione-agent-source", type=Path, default=Path("/opt/ione-codex-agent/current"))
	parser.add_argument("--officecli-bin", type=Path, default=Path("/opt/ione-codex-agent/bin/officecli"))
	parser.add_argument("--base-bridge-env", type=Path, default=Path("/etc/ione-codex-agent.env"))
	parser.add_argument("--instance-root", type=Path, default=Path("/home/zyd/services/ione-site-stacks"))
	parser.add_argument("--bridge-data-root", type=Path, default=Path("/var/lib/ione-site-stacks"))
	parser.add_argument("--run-user", default="zyd")
	args = parser.parse_args()

	if os.geteuid() != 0:
		parser.error("Run this installer as root")

	site = validate_hostname(args.site, "site")
	frontend_domain = validate_hostname(args.frontend_domain, "frontend domain")
	librechat_port = validate_port(args.librechat_port, "LibreChat port")
	bridge_port = validate_port(args.bridge_port, "bridge port")
	if librechat_port == bridge_port:
		parser.error("LibreChat and bridge ports must be different")

	slug = site_slug(site)
	project = f"ione-librechat-{slug}"
	instance = args.instance_root / slug
	bridge_data = args.bridge_data_root / slug
	bridge_env_path = Path("/etc/ione-site-stacks") / slug / "bridge.env"
	bridge_unit = f"ione-codex-agent-{slug}"
	librechat_unit = f"ione-librechat-{slug}"

	apps = installed_apps(args.bench, site)
	if "ione_agent" not in apps:
		bench(args.bench, site, "install-app", "ione_agent")

	instance.mkdir(parents=True, exist_ok=True)
	for directory in ("images", "uploads", "logs", "mongodb", "meilisearch"):
		(instance / "runtime" / directory).mkdir(parents=True, exist_ok=True)
	run(["chown", "-R", f"{args.run_user}:{args.run_user}", str(instance)])

	commit = ensure_repo(
		args.librechat_source,
		args.librechat_repository,
		args.librechat_branch,
		args.run_user,
	)
	image = f"ione/librechat:{commit}"
	try:
		run(["docker", "image", "inspect", image], capture=True)
	except subprocess.CalledProcessError:
		run(["docker", "build", "--target", "node", "--tag", image, str(args.librechat_source)])

	existing_librechat_env = parse_env(instance / ".env.ione") if (instance / ".env.ione").is_file() else {}
	bridge_token = existing_librechat_env.get("IONE_AGENT_API_TOKEN") or secrets.token_hex(32)
	sso_secret = existing_librechat_env.get("IONE_SSO_SHARED_SECRET") or secrets.token_hex(32)
	identity_secret = existing_librechat_env.get("IONE_IDENTITY_SHARED_SECRET") or secrets.token_hex(32)

	bridge_env_existing = parse_env(bridge_env_path) if bridge_env_path.is_file() else {}
	if bridge_env_existing:
		credentials = {
			"api_key": bridge_env_existing.get("IONE_FRAPPE_API_KEY", ""),
			"api_secret": bridge_env_existing.get("IONE_FRAPPE_API_SECRET", ""),
		}
	else:
		output = bench(
			args.bench,
			site,
			"execute",
			"ione_agent.setup.companion.create_integration_credentials",
			capture=True,
		)
		credentials = parse_credentials(output)
	if not credentials.get("api_key") or not credentials.get("api_secret"):
		raise RuntimeError("The protected bridge environment is missing Frappe credentials")

	librechat_env = {
		"ALLOW_EMAIL_LOGIN": "false",
		"ALLOW_PASSWORD_RESET": "false",
		"ALLOW_REGISTRATION": "false",
		"ALLOW_SHARED_LINKS": "false",
		"ALLOW_SOCIAL_LOGIN": "false",
		"APP_TITLE": "I-ONE Agent",
		"CREDS_IV": existing_librechat_env.get("CREDS_IV") or secrets.token_hex(16),
		"CREDS_KEY": existing_librechat_env.get("CREDS_KEY") or secrets.token_hex(32),
		"DEBUG_CONSOLE": "false",
		"DEBUG_LOGGING": "false",
		"DOMAIN_CLIENT": f"https://{frontend_domain}",
		"DOMAIN_SERVER": f"https://{frontend_domain}",
		"GID": "1000",
		"IONE_AGENT_API_TOKEN": bridge_token,
		"IONE_IDENTITY_SHARED_SECRET": identity_secret,
		"IONE_SSO_AUTO_REDIRECT": "true",
		"IONE_SSO_BUTTON_LABEL": "使用本站账号登录",
		"IONE_SSO_FRAPPE_HOST": site,
		"IONE_SSO_FRAPPE_URL": f"https://{site}",
		"IONE_SSO_PUBLIC_URL": f"https://{site}/agent",
		"IONE_SSO_SHARED_SECRET": sso_secret,
		"IONE_SSO_TIMEOUT_MS": "15000",
		"JWT_REFRESH_SECRET": existing_librechat_env.get("JWT_REFRESH_SECRET") or secrets.token_hex(32),
		"JWT_SECRET": existing_librechat_env.get("JWT_SECRET") or secrets.token_hex(32),
		"LIBRECHAT_BIND_IP": args.bind_ip,
		"LIBRECHAT_HOST_PORT": str(librechat_port),
		"LIMIT_CONCURRENT_MESSAGES": "true",
		"LOG_TO_FILE": "true",
		"MEILI_MASTER_KEY": existing_librechat_env.get("MEILI_MASTER_KEY") or secrets.token_hex(32),
		"NO_INDEX": "true",
		"REFRESH_TOKEN_EXPIRY": "(1000 * 60 * 60 * 24) * 7",
		"SESSION_EXPIRY": "1000 * 60 * 30",
		"TRUST_PROXY": "1",
		"UID": "1000",
	}
	atomic_write(instance / ".env.ione", env_text(librechat_env), 0o600)
	atomic_write(instance / "compose.yaml", librechat_compose(image), 0o644)
	atomic_write(instance / "librechat.yaml", librechat_config(bridge_port), 0o644)
	run(["chown", "-R", f"{args.run_user}:{args.run_user}", str(instance)])

	base_env = parse_env(args.base_bridge_env)
	bridge_env = common_bridge_env(base_env, bridge_env_existing)
	bridge_env.update(
		{
			"IONE_CODEX_BRIDGE_TOKEN": bridge_token,
			"IONE_CODEX_DATA_DIR": str(bridge_data / "data"),
			"IONE_CODEX_HOME": str(bridge_data / "codex-home"),
			"IONE_CODEX_WORKSPACE_ROOT": str(bridge_data / "workspaces"),
			"IONE_CODEX_WORKSPACE_SCOPE": "site",
			"IONE_FRAPPE_API_KEY": credentials["api_key"],
			"IONE_FRAPPE_API_SECRET": credentials["api_secret"],
			"IONE_FRAPPE_AUTH_HEADER": f"token {credentials['api_key']}:{credentials['api_secret']}",
			"IONE_FRAPPE_MCP_URL": f"https://{site}/api/method/ione_core.mcp.server.handle_mcp",
			"IONE_FRAPPE_DYNAMIC_TOOLS": "1",
			"IONE_MANAGER_IDENTITY_SECRET": identity_secret,
			"IONE_OFFICECLI_BIN": str(args.officecli_bin),
		}
	)
	officecli_version = ensure_officecli(args.officecli_bin)
	atomic_write(bridge_env_path, env_text(bridge_env), 0o600)
	for directory in (
		bridge_data,
		bridge_data / "data",
		bridge_data / "codex-home",
		bridge_data / "workspaces",
	):
		directory.mkdir(parents=True, exist_ok=True)
	run(["chown", "-R", "ioneagent:ioneagent", str(bridge_data), str(bridge_env_path.parent)])

	set_site_config(
		args.bench,
		site,
		{
			"ione_agent_companion_managed": "1",
			"ione_agent_frontend_url": f"https://{frontend_domain}",
			"ione_agent_identity_shared_secret": identity_secret,
			"ione_agent_sso_shared_secret": sso_secret,
		},
	)
	bench(args.bench, site, "clear-cache")

	atomic_write(
		Path("/etc/systemd/system") / f"{bridge_unit}.service",
		bridge_service(slug, args.ione_agent_source, bridge_env_path, bridge_data, bridge_port),
		0o644,
	)
	atomic_write(
		Path("/etc/systemd/system") / f"{librechat_unit}.service",
		librechat_service(slug, instance, project, bridge_unit, args.run_user),
		0o644,
	)
	run(["systemctl", "daemon-reload"])
	run(["systemctl", "enable", "--now", bridge_unit, librechat_unit])
	wait_for_url(f"http://{args.bind_ip}:{bridge_port}/health")
	wait_for_url(f"http://{args.bind_ip}:{librechat_port}/health", timeout=300)

	print(
		json.dumps(
			{
				"site": site,
				"frontend": f"https://{frontend_domain}",
				"librechat_commit": commit,
				"librechat_service": librechat_unit,
				"bridge_service": bridge_unit,
				"officecli_version": officecli_version,
				"status": "healthy",
			},
			ensure_ascii=False,
		)
	)
	return 0


if __name__ == "__main__":
	try:
		raise SystemExit(main())
	except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
		print(f"ERROR: {exc}", file=sys.stderr)
		raise SystemExit(1) from exc
