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
5. Create ordinary drafts with `frappe_create_document`; use `frappe_update_document` for explicit changes. Use the dedicated atomic workflow below for a new Lead.
6. Read the returned document name and summarize exactly what was persisted and what still needs human action.

For requests to list all records, call `frappe_list_documents` with only the fields needed for the
answer and a page size no larger than 100. Begin with `start=0`, then continue with the returned
`next_start` while `has_more` is true. Do not request descriptions or attachment bodies for a
summary list. Preserve the user's request for all records instead of silently stopping at the first
page.

When the request combines a detailed customer proposal, Word output, Lead conversion and Deal
attachment, load and follow the `lead-proposal-to-deal` skill instead of improvising the sequence.

## Creating a Lead

When the user asks to create, collect, discover or import a CRM Lead, treat the Lead, requirements
analysis and first follow-up task as one business package:

1. Search existing `CRM Lead` records first. Use source URL, organization, email and phone to avoid
   duplicates. Update an existing Lead only when the user explicitly asks.
2. Read `CRM Lead` metadata and map only supported fields. Preserve source URL, source title,
   publication date, contact details, organization, industry and region whenever available.
3. Separate source facts from analysis. Never invent contacts, budget, procurement status, dates or
   customer statements. Mark missing information as `待确认` and state how it should be verified.
4. Prepare a detailed Chinese requirements analysis for Word. It must contain at least 1,200
   non-whitespace characters, at least two management-summary paragraphs and at least ten sections.
   Cover these topics when relevant:
   - 客户与线索概况
   - 业务背景与触发事件
   - 显性需求
   - 隐性需求与根因
   - 现状流程与核心痛点
   - 目标与可量化成功指标
   - 利益相关者、使用者与决策链
   - 需求优先级及理由
   - 建议解决思路和价值假设
   - 数据、系统集成、安全与合规要求
   - 风险、分析假设和待确认事项
   - 推荐沟通问题与分阶段跟进计划
5. Put comparable facts, priorities, stakeholders, risks or follow-up actions in compact tables.
   Use prose and real bullet lists for analysis; do not fill tables with long paragraphs. Include a
   `sources` list with source name, URL when available, and access/publication date.
6. Define one concrete first task. The title must describe the action; the description must include
   objective, inputs, expected result and key questions. Default the due date to three days and use
   `High` priority only when an actual deadline or material risk supports it.
7. Call `frappe_create_crm_lead_package` once with `lead_data`, the full structured `analysis` and
   task fields. The current Manager identity is injected by I-ONE infrastructure at call time; never
   request or provide `actor_token`. The tool derives the assignee from the current Manager login;
   do not accept or invent an assignee field.
8. If the tool reports that the login identity is missing or rejected, do not fall back to the MCP
   service account. Ask the user to enter I-ONE Agent from Manager and retry.
9. Read back both the returned `CRM Lead` and `CRM Task`, list the Lead attachments, and report the
   exact Lead name, Word file, task name, assignee and due date. Do not claim success if any of these
   checks fails.

The atomic tool rolls back the Lead, Word attachment, CRM Task and assignment together when any
step fails. Do not recreate individual pieces after an atomic failure unless the user asks you to
diagnose and retry the whole package.

## Rules

- Preserve source URLs and evidence in normal fields and the private Word requirements analysis.
- Do not convert a lead to a deal unless the user explicitly asks and the available tool supports that operation.
- Do not submit, delete, approve or bulk overwrite records.
- New Lead analysis must be a detailed `.docx` file created by `frappe_create_crm_lead_package`;
  never use Markdown as the primary analysis deliverable.

See [references/data-contract.md](references/data-contract.md) for the shared MCP contract.
