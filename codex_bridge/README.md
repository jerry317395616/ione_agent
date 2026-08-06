# I-ONE Codex Bridge

This service exposes the small OpenAI-compatible surface that LibreChat needs
and delegates every conversation turn to the open-source Codex App Server.

It deliberately contains no intent router, LangGraph graph, business workflow,
Frappe tool client, UFO, Hermes, OpenClaw, or fallback agent. Codex App Server is
the only agent runtime and DeepSeek is its model provider through the Responses
API.

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

## Required environment

See `ione-codex-agent.env.example`. Secrets must be supplied by the service
manager and must not be committed.
