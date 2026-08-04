# I-ONE Agent

I-ONE Agent is a focused Frappe application that provides one enterprise Agent conversation workspace. It stores users, sessions, messages and execution records in Frappe while delegating Agent execution to the official UFO3 `main` runtime through the bundled FastAPI gateway.

## Architecture

- Frappe: authentication, permissions, session/message persistence and the `/agent` user interface.
- I-ONE UFO Gateway: isolated Python 3.10 service that owns UFO3 execution and run events.
- UFO3: pinned from the official Microsoft UFO repository `main` branch during the gateway build.
- Qwen: OpenAI-compatible model endpoint supplied through gateway environment variables.

Secrets must be configured in Frappe `site_config.json` and the gateway environment. They must never be committed.

## Frappe configuration

```json
{
  "ione_agent_gateway_url": "http://10.144.133.1:8098",
  "ione_agent_gateway_token": "replace-with-a-long-random-token"
}
```

## Gateway configuration

Copy `gateway/.env.example` to a protected deployment environment and fill in the values. See `gateway/README.md` for runtime details.
