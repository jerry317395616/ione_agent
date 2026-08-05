# I-ONE UFO Gateway

The gateway isolates Microsoft UFO3 from the Frappe runtime. It accepts authenticated asynchronous runs, executes them serially through the official UFO3 `main` branch and persists run state in SQLite.

The image uses `ufo-gateway-requirements.txt`, which keeps Galaxy orchestration, AIP, MCP and OpenAI-compatible inference while excluding local embedding/training and GPU packages that the gateway does not use.

## Required environment

- `IONE_GATEWAY_TOKEN`: long random token shared only with the Frappe site configuration.
- `IONE_DEVICE_SERVER_API_KEY`: separate internal key used between Galaxy and the UFO device server.
- `IONE_DEVICE_PUBLIC_WS_URL`: public TLS WebSocket URL used by paired Windows clients.
- `QWEN_API_BASE`: OpenAI-compatible base URL, for example `http://10.144.133.1:1234/v1`.
- `QWEN_API_KEY`: Qwen endpoint API key.
- `QWEN_MODEL`: served model name.

The HTTP run APIs must remain protected by `IONE_GATEWAY_TOKEN`. When Windows devices are enabled, publish only the TLS hostname configured by `IONE_DEVICE_PUBLIC_WS_URL`; the public WebSocket route validates a separate per-device token and proxies to the internal UFO server. Uvicorn access logs are disabled so device tokens are not written to gateway logs.

## Windows device pairing

The Frappe `/agent` page creates a short-lived installer. The installer pairs once with Frappe, stores its connection URL using Windows DPAPI, installs the official UFO `main` branch in a Python 3.10 environment and starts the client at user logon. Desktop automation therefore runs in the interactive Windows session rather than Session 0.

Route the hostname in `IONE_DEVICE_PUBLIC_WS_URL` to gateway port `8098` through your TLS reverse proxy or Cloudflare Tunnel. The Windows client makes an outbound-only `wss://` connection and does not require an inbound firewall rule.

## Run

```bash
cp .env.example .env
docker compose up -d --build
curl http://127.0.0.1:8098/health
```

UFO3 is cloned from the official repository's `main` branch during image build. The exact commit is reported by `/health` and stored on every Frappe run record.

For restricted server networks, place a verified official checkout at `vendor/UFO` and set `IONE_GATEWAY_DOCKERFILE=Dockerfile.offline`. The vendor directory is intentionally excluded from Git.
