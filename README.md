# I-ONE Agent

I-ONE Agent is a Frappe application for enterprise conversations, verifiable lead intelligence and controlled desktop execution. It stores users, tasks, evidence, candidates and execution records in Frappe while delegating long-running work to isolated services.

## Architecture

- Frappe: authentication, permissions, session/message persistence and the `/agent` user interface.
- Dify: published chat application, workflow/agent orchestration and its own administrator console.
- Lead Intelligence Orchestrator: governed LangGraph model/tool loop with persistent checkpoints, typed tools, policy enforcement and idempotent result synchronization.
- Hermes Agent: internet research against configured official tender and procurement sources.
- DeepSeek: first-stage planning model and final specialist reviewer for qualified leads.
- Qwen: stable execution controller, structured extractor, scoring model and planning fallback.
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
  "ione_agent_dify_base_url": "https://dify.example.com/v1",
  "ione_agent_dify_api_key": "app-secret-from-a-published-dify-chat-app",
  "ione_agent_dify_user_secret": "replace-with-a-long-random-secret",
  "ione_agent_dify_model_label": "Qwen3-35B-A3B-FP8",
  "ione_agent_gateway_url": "http://10.144.133.1:8098",
  "ione_agent_gateway_token": "replace-with-a-long-random-token",
  "ione_agent_orchestrator_url": "http://10.144.133.1:8100",
  "ione_agent_orchestrator_token": "replace-with-a-different-long-random-token"
}
```

Normal chat is sent from the Frappe backend to the published Dify app. The Dify API key never reaches
the browser, and Frappe users are represented in Dify by stable HMAC identifiers instead of email
addresses. Dify task, message, workflow and conversation identifiers are stored with the Frappe run
for permission checks and auditing. Lead discovery and controlled desktop execution retain their
specialized runtimes.

## Gateway configuration

Copy `gateway/.env.example` to a protected deployment environment and fill in the values. See `gateway/README.md` for runtime details.

## Natural-language lead discovery

Users can ask, for example, “寻找近 30 天医疗行业的招标和采购线索，分析需求并整理到候选线索池”。 The request is routed to the LangGraph orchestrator instead of the desktop executor. Results are first stored in **AI 获客 > 候选线索** with source URLs, evidence, confidence and model status. CRM Leads are created only when the profile enables automatic creation or a user explicitly confirms a candidate.

The orchestrator is deployed from `orchestrator/`. Copy `.env.example` to a protected environment file and never commit tokens. Production deployments should configure `IONE_CHECKPOINT_DATABASE_URL` for PostgreSQL. SQLite WAL remains available for development and single-process recovery.

## LibreChat frontend

The production chat frontend is maintained in `jerry317395616/LibreChat`, a fork of the actual
LibreChat application. The similarly named `librechat.ai` repository is the documentation website
and is intentionally not used as the chat runtime.

LibreChat connects to this service as an OpenAI-compatible custom endpoint at `/v1`. The bridge
routes greetings, questions and planning conversations to DeepSeek with bounded conversation
history. Requests that need internet search, tools or business-data changes continue through the
existing Frappe `ione_agent.api` methods, while SSE comments keep long tasks alive. This preserves
Frappe sessions, run audits, lead tasks, candidate records and CRM writes for executable work.
Configure a distinct
`IONE_LIBRECHAT_API_TOKEN` plus a dedicated Frappe integration user's API credentials; do not reuse
browser passwords or commit credentials. Set `ione_agent_frontend_url` in the Frappe site config to
the deployed LibreChat URL when the new frontend is ready.

For production installation, use `deploy/install_site_companion.py` instead of granting the Frappe
container access to the host Docker socket. The installer installs `ione_agent`, obtains the current
LibreChat fork, creates per-site Compose and bridge services, assigns unique ports and secrets, enables
both services at boot, and verifies their health. See `deploy/README.md` for the command and isolation
contract.

## Production agent controls

- DeepSeek API is the primary LLM for planning, intent parsing, tool selection, evidence analysis and final lead review. The validated state is persisted by LangGraph rather than entrusted to the model.
- `deepseek-v4-pro` handles planning and final review. `deepseek-v4-flash` handles frequent structured parsing, analysis and native tool selection.
- DeepSeek calls are bounded by per-node timeouts, retry only transient failures and open a circuit breaker after repeated failures.
- Qwen is the automatic model fallback. A deterministic evidence-only path still completes safely when both providers are unavailable.
- The control node uses DeepSeek native Tool Calls, then validates the selected tool against the plan, dependency graph, Pydantic arguments and policy allowlist before execution.
- Hermes only receives the requested candidate set with bounded source text. `HERMES_REQUEST_TIMEOUT_SECONDS` defaults to 240 seconds; a timeout preserves collected evidence and lets Qwen continue the run.
- Every tool is versioned, allowlisted, validated and assigned a risk level before execution.
- Tool side effects use a deterministic idempotency key and are recorded in the orchestrator audit database.
- `/v1/runs/{run_id}/trace` exposes the authenticated model/tool trace and `/metrics` exposes operational counters.
