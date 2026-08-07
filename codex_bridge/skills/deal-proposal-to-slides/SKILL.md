---
name: deal-proposal-to-slides
description: Turn a CRM Deal's detailed Word proposal into an editable, customer-facing commercial presentation in Frappe Suite Slides. Use when the user asks to make a customer PPT, presentation, roadshow deck, proposal deck, or Slides presentation from a specific business opportunity or its proposal attachment.
---

# Deal Proposal To Slides

Create a concise customer narrative from verified CRM Deal data and its Word proposal, then save it as an editable Frappe Suite Slides presentation linked to the Deal.

## Workflow

1. Call `frappe_get_context`. Confirm the current site has `crm`, `ione_core`, and `suite` installed. Suite supplies the Slides module; do not require or install a separate `slides` app.
2. Resolve one exact `CRM Deal` name. If the user supplied only a customer or project name, search and list plausible Deals; do not guess.
3. Read the Deal with `frappe_get_document`. Treat CRM fields as authoritative for customer identity, amount, stage, owner, and dates.
4. Call `frappe_list_attachments` and select the most recent relevant `.docx` proposal. Prefer names beginning with `proposal_`; when multiple documents are equally plausible, ask the user which one to use.
5. Read the selected document with `frappe_read_word_attachment`. Do not fetch private attachment URLs through a browser or shell.
6. Build an 8-12 slide customer narrative. Read [references/presentation-contract.md](references/presentation-contract.md) before composing the payload.
7. Call `frappe_upsert_deal_presentation` once with the exact Deal name, presentation title, and complete slide array. Omit `make_public` unless the user explicitly asks for public link access.
8. Verify the returned Deal, presentation name, slide count, editor URL, and slideshow URL. Report those exact values.

## Content Rules

- Write for the customer's executives and project stakeholders, not for internal sales review.
- Lead with the customer's situation and desired outcomes. Present I-ONE capabilities only as responses to those needs.
- Preserve facts from the Deal and proposal. Never invent customer names, prices, dates, scope commitments, performance guarantees, regulations, or quantified benefits.
- Put uncertain content behind explicit wording such as “建议目标”“待双方确认” or “以项目调研结果为准”.
- Use one message per slide, short titles, and no more than six concise bullets.
- Use `metrics` only for sourced numbers or clearly labeled proposed targets.
- Include delivery approach, governance, risks/assumptions, and a concrete next step.
- Do not copy long proposal paragraphs into slides. Synthesize them.

## Safety And Idempotency

- Creating or updating the customer presentation is the only write operation authorized by this skill.
- The Slides MCP tool reuses the presentation linked to the Deal. Never create a second presentation to recover from an error.
- Keep presentations private by default. Public access requires explicit user instruction.
- Do not modify the source Word proposal, Deal stage, amount, owner, or any approval state.
- If Suite Slides is unavailable, report the exact prerequisite failure. Do not attach a fake PPT or claim success.
