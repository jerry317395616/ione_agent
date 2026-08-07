---
name: crm-sales
description: Use this skill for Frappe CRM lead, deal, contact, organization, follow-up, note, and task work on the manager site. It defines a permission-aware read-first workflow for finding, creating, updating, and verifying CRM drafts through MCP.
---

# CRM Sales

Use the manager MCP tools as the only CRM data interface. Never invent a record name or claim a write succeeded without reading the saved document back.

## Workflow

1. Call `frappe_get_context` when the current identity or roles matter.
2. Call `frappe_search_doctypes` with `CRM`, `Lead`, `Deal`, `Contact`, or `Organization` instead of guessing installed DocType names.
3. Read `frappe_get_doctype_meta` before the first write to each DocType in a conversation.
4. Search existing records before creating one. Treat source URL, organization, email and phone as possible duplicate keys.
5. Create only drafts with `frappe_create_document`; use `frappe_update_document` for explicit changes.
6. Read the returned document name and summarize exactly what was persisted and what still needs human action.

## Rules

- Preserve source URLs and evidence in normal fields or a private Markdown attachment.
- Do not convert a lead to a deal unless the user explicitly asks and the available tool supports that operation.
- Do not submit, delete, approve or bulk overwrite records.
- For a lead-analysis report, attach a concise `.md` file to the lead and retain source attribution.

See [references/data-contract.md](references/data-contract.md) for the shared MCP contract.
