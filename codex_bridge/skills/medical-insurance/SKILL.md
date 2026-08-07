---
name: medical-insurance
description: Use this skill for the I-ONE medical-insurance operations application, including governed searches, operational indicators, anomalies, evidence, and analysis records. It requires metadata discovery, minimum necessary data access, and verifiable outputs through Frappe MCP.
---

# Medical Insurance Operations

Use this Skill for 医保智能运营 questions and business records. Treat health and insurance information as sensitive data and retrieve only fields needed for the user's task.

## Workflow

1. Search DocTypes with `医保`, `Medical Insurance`, `结算`, `规则`, or the user's functional term.
2. Inspect metadata and identify the correct application-owned record; do not guess schema names.
3. Query the smallest useful record set with narrow filters and a bounded limit.
4. Explain findings using aggregated values when person-level detail is unnecessary.
5. Save requested analyses as drafts or private Markdown evidence attachments.
6. Read back every saved result and state its exact record name.

## Safety

- Do not expose credentials, identity numbers, clinical details or payment data beyond the authorized task.
- Do not change settlement, audit, payment or enforcement states.
- Distinguish observed data from model inference and preserve evidence links or record names.
- Ask for a narrower scope when a request would retrieve excessive personal data.

See [references/data-contract.md](references/data-contract.md).
