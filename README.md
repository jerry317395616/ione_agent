# I-ONE Agent

I-ONE Agent is a Frappe application for enterprise conversations, verifiable lead intelligence and controlled desktop execution. It stores users, tasks, evidence, candidates and execution records in Frappe while delegating long-running work to isolated services.

## Architecture

- Frappe: authentication, permissions, session/message persistence and the `/agent` user interface.
- Lead Intelligence Orchestrator: governed LangGraph model/tool loop with persistent checkpoints, typed tools, policy enforcement and idempotent result synchronization.
- Hermes Agent: internet research against configured official tender and procurement sources.
- Qwen: primary model for intent parsing, structured extraction, scoring and summaries.
- DeepSeek: auxiliary reviewer that produces the sales pursuit plan attached to each qualified lead.
- I-ONE UFO Gateway: isolated Python 3.10 service that owns UFO3 execution and run events.
- UFO3: pinned from the official Microsoft UFO repository `main` branch during the gateway build.
- Qwen: OpenAI-compatible model endpoint supplied through gateway environment variables.
- Windows Device Client: one-time pairing package that installs the official UFO `main` client in the current user's interactive session.

Secrets must be configured in Frappe `site_config.json` and the gateway environment. They must never be committed.

## Connect a Windows computer

Open `/agent`, select **连接电脑**, and generate the short-lived Windows installer. After the user downloads, extracts, and runs `install.cmd` once, the installer:

1. Claims the one-time pairing token.
2. Stores the per-device WebSocket URL with Windows DPAPI.
3. Installs Python 3.10 and the official Microsoft UFO `main` branch.
4. Registers a per-user logon task and starts the interactive desktop client.

The computer makes an outbound-only TLS WebSocket connection. It does not expose a local port. Revoking the device from `/agent` immediately prevents future gateway connections.

## Frappe configuration

```json
{
  "ione_agent_gateway_url": "http://10.144.133.1:8098",
  "ione_agent_gateway_token": "replace-with-a-long-random-token",
  "ione_agent_orchestrator_url": "http://10.144.133.1:8100",
  "ione_agent_orchestrator_token": "replace-with-a-different-long-random-token"
}
```

## Gateway configuration

Copy `gateway/.env.example` to a protected deployment environment and fill in the values. See `gateway/README.md` for runtime details.

## Natural-language lead discovery

Users can ask, for example, “寻找近 30 天医疗行业的招标和采购线索，分析需求并整理到候选线索池”。 The request is routed to the LangGraph orchestrator instead of the desktop executor. Results are first stored in **AI 获客 > 候选线索** with source URLs, evidence, confidence and model status. CRM Leads are created only when the profile enables automatic creation or a user explicitly confirms a candidate.

The orchestrator is deployed from `orchestrator/`. Copy `.env.example` to a protected environment file and never commit tokens. Production deployments should configure `IONE_CHECKPOINT_DATABASE_URL` for PostgreSQL. SQLite WAL remains available for development and single-process recovery.

## Production agent controls

- Qwen is the default controller because it exposes a stable OpenAI-compatible interface.
- DeepSeek web can be selected with `IONE_AGENT_CONTROL_MODEL=deepseek`; malformed output or browser failure falls back to Qwen.
- DeepSeek remains the specialist reviewer for qualified leads and uses an isolated browser conversation for every call.
- Hermes only receives the requested candidate set with bounded source text. `HERMES_REQUEST_TIMEOUT_SECONDS` defaults to 240 seconds; a timeout preserves collected evidence and lets Qwen continue the run.
- Every tool is versioned, allowlisted, validated and assigned a risk level before execution.
- Tool side effects use a deterministic idempotency key and are recorded in the orchestrator audit database.
- `/v1/runs/{run_id}/trace` exposes the authenticated model/tool trace and `/metrics` exposes operational counters.
