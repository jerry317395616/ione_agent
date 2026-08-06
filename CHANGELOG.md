# Changelog

## 0.4.0

- Replace the fixed lead pipeline with a governed LangGraph model/tool loop.
- Add versioned tool schemas, allowlisting, risk policy, parameter validation and idempotent replay.
- Add Qwen controller routing with optional DeepSeek web control and automatic Qwen fallback.
- Add DeepSeek circuit breaking, unique browser conversations and Qwen review fallback.
- Add durable model/tool audit trails, authenticated run traces and Prometheus-style metrics.
- Add PostgreSQL checkpoint support with SQLite WAL fallback for single-node deployments.

## 0.3.0

- Add natural-language lead and tender discovery with a persistent LangGraph orchestrator.
- Add Hermes research, Qwen extraction and DeepSeek auxiliary review adapters.
- Add lead profiles, trusted sources, discovery tasks and evidence-backed candidates.
- Add controlled, traceable CRM Lead creation without modifying Frappe CRM source.
- Add the Chinese AI 获客 workspace and a lead-discovery starter in `/agent`.

## 0.2.13

- Fall back to Win32 window restoration and focus when UFO's standard UI focus call fails.

## 0.2.12

- Allow long blocking desktop actions without triggering Uvicorn's short WebSocket ping timeout.

## 0.2.11

- Send UFO desktop application heartbeats every 20 seconds through Cloudflare Tunnel.
- Reapply the local heartbeat compatibility patch after each UFO main update.

## 0.2.10

- Keep public desktop WebSockets active during long UFO planning and execution.
- Serialize concurrent proxy and heartbeat messages on each device connection.

## 0.2.9

- Record which side closes a desktop-device proxy connection.
- Preserve UFO device-server warnings in gateway diagnostics.

## 0.2.8

- Keep the local UFO device proxy alive during long model-planning calls.
- Correct UFO Galaxy heartbeat role metadata without modifying upstream UFO source.

## 0.2.7

- Correctly treat nested UFO3 constellation failures as failed runs.
- Prefer user-facing final results and summarize structured responses instead of exposing raw JSON.
- Pin the UFO3 gateway to the compatible WebSocket client API so desktop tasks can be dispatched.

## 0.2.6

- Cache-bust the Agent page assets using the application version.
- Open device management immediately while refreshing devices in the background.

## 0.2.5

- Move the UFO device-server watchdog to an independent thread.
- Probe the complete WebSocket handshake with an operating-system timeout.
- Restart the gateway container when the internal device service becomes unresponsive.

## 0.2.4

- Enforce a single Windows desktop executor per signed-in user.
- Clean up orphaned UFO clients when reinstalling or reconnecting.
- Add device-server watchdog recovery for stalled WebSocket sessions.

## 0.2.3

- Use Python 3.10 for compatibility with UFO's pinned Windows dependencies.
- Recreate only the virtual environment when an incompatible Python version is detected.

## 0.2.2

- Trim the DPAPI ciphertext before loading the Windows device configuration.

## 0.2.1

- Fix Windows PowerShell 5 compatibility when downloading the uv installer.
- Fix device configuration ACL assignment on Windows.

## 0.2.0

- Added short-lived Windows device pairing and per-device token revocation.
- Added the central authenticated UFO2 WebSocket relay and dynamic Galaxy device registry.
- Added a one-click Windows bootstrap with DPAPI-protected settings and logon startup.
- Added device status and management controls to the `/agent` interface.

## 0.1.0

- Initial Frappe Agent conversation workspace.
- Persistent sessions, messages and execution runs.
- UFO3 gateway with Qwen-compatible configuration.
