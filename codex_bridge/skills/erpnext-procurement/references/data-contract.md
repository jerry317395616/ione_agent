# MCP data contract

- Discover DocTypes and metadata before writing.
- Reads and writes run with the integration user's Frappe permissions.
- Only draft create/update and private text attachments are available.
- Submission, cancellation, deletion, payment, SQL and arbitrary RPC are unavailable.
- Verify every write by reading the saved document.
