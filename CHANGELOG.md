# Changelog

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
