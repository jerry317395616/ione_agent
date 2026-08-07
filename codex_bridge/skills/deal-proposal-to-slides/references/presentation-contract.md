# Customer Presentation Contract

## Recommended Narrative

Use 8-12 slides in this order unless the source proposal clearly needs a different sequence:

1. Cover: solution name, customer, presentation purpose.
2. Customer context: current situation and business pressure.
3. Needs and goals: verified needs, success conditions, stakeholders.
4. Solution overview: the proposed response and its boundaries.
5. Capability scope: business modules, workflows, data, and integrations.
6. Delivery roadmap: phases from discovery through operation.
7. Expected value: sourced outcomes or explicitly proposed target metrics.
8. Delivery and governance: roles, cadence, quality, security, and acceptance.
9. Assumptions and next steps: items requiring confirmation and immediate actions.
10. Closing: shared objective and proposed next meeting/action.

## Tool Payload

`frappe_upsert_deal_presentation` accepts 4-20 slide objects. Prefer 8-12.

```json
{
  "deal": "CRM-DEAL-2026-00001",
  "title": "客户名称 - 项目建设方案",
  "slides": [
    {
      "kind": "cover",
      "title": "区域医疗智能运营平台建设方案",
      "subtitle": "面向客户决策与项目启动沟通",
      "callout": "客户名称 | 2026年8月",
      "footer": "I-ONE AI"
    },
    {
      "kind": "content",
      "title": "我们理解的核心需求",
      "subtitle": "以下内容来自商机资料和方案附件",
      "bullets": ["统一业务数据口径", "提升运营分析效率"],
      "callout": "最终范围以双方调研确认结果为准",
      "footer": "客户名称"
    },
    {
      "kind": "metrics",
      "title": "建议的价值衡量方式",
      "metrics": [
        {"value": "待确认", "label": "效率目标", "detail": "在项目启动阶段共同设定基线"}
      ]
    },
    {
      "kind": "timeline",
      "title": "实施路径",
      "bullets": ["需求调研", "方案配置", "试点验证", "推广运营"]
    },
    {
      "kind": "closing",
      "title": "携手推进下一步",
      "subtitle": "从范围确认与试点计划开始",
      "bullets": ["确认业务范围", "确定项目团队", "安排启动会议"]
    }
  ]
}
```

## Layout Kinds

- `cover`: First slide only. Use title, subtitle, callout, footer.
- `section`: Optional chapter divider. Use sparingly.
- `content`: Needs, solution scope, governance, assumptions. Use bullets and optional callout.
- `metrics`: Up to four metrics with `value`, `label`, and optional `detail`.
- `timeline`: Two to four ordered phase labels in `bullets`.
- `closing`: Final slide with next actions.

Every slide requires `title`. Limits are enforced server-side: six bullets, four metrics, 180 characters per bullet, and 20 slides total.
