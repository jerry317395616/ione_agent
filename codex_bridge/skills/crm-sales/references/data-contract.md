# MCP data contract

- Discover DocTypes and metadata before writing.
- All reads use the authenticated Frappe user's permissions.
- Writes are draft-only and are recorded in `I-ONE MCP Audit Log`.
- Available writes are create, update and private UTF-8 text attachment.
- Delete, submit, cancel, SQL and arbitrary method execution are unavailable.
- After every write, read the saved document and report its exact DocType and name.
