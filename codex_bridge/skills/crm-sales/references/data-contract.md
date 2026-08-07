# MCP data contract

- Discover DocTypes and metadata before writing.
- All reads use the authenticated Frappe user's permissions.
- Writes are draft-only and are recorded in `I-ONE MCP Audit Log`.
- Available writes are draft create/update, private UTF-8 text attachment, validated private Word
  attachment, and the dedicated idempotent CRM Lead-to-Deal conversion.
- Lead and Deal attachments can be listed. Small UTF-8 text attachments can be read when the parent
  document is readable.
- Delete, submit, cancel, SQL and arbitrary method execution are unavailable.
- After every write, read the saved document and report its exact DocType and name.
