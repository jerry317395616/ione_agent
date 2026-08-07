---
name: erpnext-procurement
description: Use this skill for ERPNext suppliers, items, material requests, requests for quotation, supplier quotations, purchase orders, purchase receipts, and purchase invoices. It provides a controlled draft-first procurement workflow through Frappe MCP.
---

# ERPNext Procurement

Use this Skill when the user asks about purchasing, suppliers, requisitions, quotations, orders, receipts or invoices.

## Workflow

1. Discover the exact installed DocType and inspect its metadata.
2. Read company, supplier, item, warehouse, currency and naming context before constructing a transaction.
3. Search for an existing document using business keys before creating a duplicate.
4. Create or update a draft. Include child rows only with field names confirmed by metadata.
5. Read the saved document and report missing mandatory values, totals, status and next human step.

## Controls

- Never submit, cancel, delete, pay or reconcile a document.
- Never fabricate item codes, suppliers, tax templates, warehouses or accounts.
- Do not silently replace quantities, rates, schedules or taxes.
- If prerequisites are missing, explain them instead of creating placeholder master data unless the user explicitly asks.

See [references/data-contract.md](references/data-contract.md).
