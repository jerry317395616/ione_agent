# Proposal payload schema

Write one UTF-8 JSON object for `render_proposal.py`.

## Required top-level fields

```json
{
  "proposal_title": "项目建设方案",
  "customer_name": "客户或机构名称",
  "lead_name": "CRM-LEAD-2026-00011",
  "prepared_by": "I-ONE AI",
  "date": "2026-08-07",
  "version": "V1.0",
  "confidentiality": "商业机密，仅供项目沟通使用",
  "executive_summary": ["摘要段落一", "摘要段落二"],
  "sections": [],
  "sources": ["CRM Lead: CRM-LEAD-2026-00011", "附件: lead_analysis_....md"]
}
```

The renderer requires at least 10 sections and at least 1,200 non-whitespace content characters. A detailed production proposal should normally contain 14 to 18 sections and 3,000 or more Chinese characters.

## Section shape

```json
{
  "title": "需求理解",
  "paragraphs": ["对客户现状和目标的完整说明。"],
  "bullets": ["已确认事实：...", "合理推断：..."],
  "tables": [
    {
      "title": "需求响应矩阵",
      "headers": ["需求", "方案响应", "验证方式"],
      "rows": [["需求一", "响应设计", "验收测试"]]
    }
  ]
}
```

Every section needs a title and at least one paragraph, bullet, or table. Use normal paragraphs for explanation, bullets for grouped points, and tables only for repeated comparable records.

## Recommended section order

1. 项目背景与客户现状
2. 客户需求理解
3. 建设目标与成功指标
4. 建设范围
5. 总体解决方案
6. 功能设计
7. 数据架构与数据治理
8. 系统集成方案
9. 安全、隐私与合规
10. 实施方法与里程碑
11. 项目组织与职责
12. 测试、验收与上线
13. 培训、运维与服务保障
14. 交付物清单
15. 商务与周期假设
16. 风险与应对措施
17. 前提条件、边界与不包含项
18. 待确认问题与下一步

## Evidence discipline

- Put verified lead values and attachment facts in normal statements.
- Prefix unsupported but useful planning statements with `方案假设：`.
- Put unresolved customer questions in the final section.
- Use `待双方确认` instead of inventing a value.
- Do not cite a source URL unless it exists in the lead or attachment evidence.
