# I-ONE UFO Gateway

The gateway isolates Microsoft UFO3 from the Frappe runtime. It accepts authenticated asynchronous runs, executes them serially through the official UFO3 `main` branch and persists run state in SQLite.

## Required environment

- `IONE_GATEWAY_TOKEN`: long random token shared only with the Frappe site configuration.
- `QWEN_API_BASE`: OpenAI-compatible base URL, for example `http://10.144.133.1:1234/v1`.
- `QWEN_API_KEY`: Qwen endpoint API key.
- `QWEN_MODEL`: served model name.

The service must bind to a private address. Do not expose port 8098 through Cloudflare or the public internet.

## Run

```bash
cp .env.example .env
docker compose up -d --build
curl http://127.0.0.1:8098/health
```

UFO3 is cloned from the official repository's `main` branch during image build. The exact commit is reported by `/health` and stored on every Frappe run record.

