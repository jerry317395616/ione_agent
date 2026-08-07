# I-ONE Codex Bridge

This service exposes the small OpenAI-compatible surface that LibreChat needs
and delegates every conversation turn to the open-source Codex App Server.

It deliberately contains no intent router, LangGraph graph, UFO, Hermes,
OpenClaw, or fallback agent. Codex App Server is the only agent runtime and
DeepSeek is its model provider through the Responses API. Business operations
are exposed by the official Frappe MCP package in `ione_core`; this bridge only
configures that authenticated endpoint and installs versioned business Skills.

The production default is `deepseek-v4-flash`. The model remains configurable
through `IONE_CODEX_MODEL`, so `deepseek-v4-pro` can be enabled after DeepSeek
opens Codex integration for that model.

## Runtime contract

- `GET /health` and `GET /ready` are unauthenticated service probes.
- `GET /v1/models` and `POST /v1/chat/completions` use a bearer token.
- LibreChat conversation IDs are mapped to persisted Codex thread IDs in
  SQLite. Codex itself persists thread history under `CODEX_HOME`.
- Streaming requests emit OpenAI-compatible SSE chunks and periodic comments,
  so long-running tool work does not depend on a short proxy timeout.
- Client cancellation interrupts the active Codex turn.
- Frappe credentials stay in the protected service environment. They are never
  written to `config.toml`, conversation history or a Skill.
- The initial MCP surface supports permission-aware reads, draft writes and
  private text attachments. Submit, cancel, delete, SQL and arbitrary RPC are
  intentionally absent.

## Required environment

See `ione-codex-agent.env.example`. Secrets must be supplied by the service
manager and must not be committed.

Use a dedicated Frappe integration user rather than `Administrator`. Set
`IONE_FRAPPE_AUTH_HEADER` to `token api_key:api_secret` and grant that user only
the roles needed by the enabled Skills. Every MCP tool call is recorded in
`I-ONE MCP Audit Log` on the manager site.
