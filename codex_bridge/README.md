# I-ONE Codex Bridge

This service exposes the small OpenAI-compatible surface that LibreChat needs
and delegates every conversation turn to the open-source Codex App Server.

It deliberately contains no intent router, LangGraph graph, UFO, Hermes,
OpenClaw, or fallback agent. Codex App Server is the only agent runtime. The
production child-site profile connects it directly to the Qwen Responses API
gateway on the server's private network. Business operations are exposed by
the official Frappe MCP package in `ione_core`; this bridge only configures that
authenticated endpoint and installs versioned business Skills.

The production default is `qwen3.6-35b-a3b-fp8`. `IONE_MODEL_API_BASE` is
validated at startup and public model hosts are rejected unless the explicit
private-network guard is disabled for an isolated development profile. Legacy
`QWEN_*` and `DEEPSEEK_*` variables are read only as migration fallbacks; new
deployments write the neutral `IONE_MODEL_*` variables.

## Runtime contract

- `GET /health` and `GET /ready` are unauthenticated service probes.
- `GET /v1/models` and `POST /v1/chat/completions` use a bearer token.
- LibreChat conversation IDs are mapped to persisted Codex thread IDs in
  SQLite. Codex itself persists thread history under `CODEX_HOME`.
- All users on one Frappe site share the same controlled site workspace while
  conversation and login identity remain isolated. Business reads and writes
  are always authorized again by Frappe MCP for the current login.
- Streaming requests emit OpenAI-compatible SSE chunks and periodic comments,
  so long-running tool work does not depend on a short proxy timeout.
- Client cancellation interrupts the active Codex turn.
- Frappe credentials stay in the protected service environment. They are never
  written to `config.toml`, conversation history or a Skill.
- The initial MCP surface supports permission-aware reads, draft writes and
  private text attachments. Submit, cancel, delete, SQL and arbitrary RPC are
  intentionally absent.
- Nutrition and workbook analysis are model-designed but must be executed with
  Python or Excel formulas, checked, validated and returned as a real workbook.
  The Agent never treats mental arithmetic as a verified business result.

## Required environment

See `ione-codex-agent.env.example`. Secrets must be supplied by the service
manager and must not be committed.

For production, keep `IONE_AGENT_RUNTIME=codex`,
`IONE_MODEL_REQUIRE_PRIVATE_NETWORK=1`, and point `IONE_MODEL_API_BASE` at the
server-internal Qwen gateway (for example `http://10.144.133.1:1234`). Do not
use a public domain for the model endpoint. `IONE_CODEX_NETWORK_ACCESS=false`
also prevents shell tasks from using the network; it does not block the model
provider connection managed by Codex App Server.

Use a dedicated Frappe integration user rather than `Administrator`. Set
`IONE_FRAPPE_AUTH_HEADER` to `token api_key:api_secret` and grant that user only
the roles needed by the enabled Skills. Every MCP tool call is recorded in
`I-ONE MCP Audit Log` on the manager site.

Set `IONE_MANAGER_IDENTITY_SECRET` to the same 32+ character random value as
`ione_agent_identity_shared_secret` in the Manager site configuration. LibreChat sends the
authenticated Manager email in a server-side header; the bridge signs a short-lived assertion and
the Lead creation tool uses it only to assign the new CRM task to that logged-in Manager account.
The model cannot choose another assignee and the integration user is never used as a fallback.
