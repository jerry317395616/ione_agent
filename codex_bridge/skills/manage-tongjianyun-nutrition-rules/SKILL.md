---
name: manage-tongjianyun-nutrition-rules
description: 用自然语言安全维护童健云周食谱营养分析的版本化计算规则。涉及营养公式、可食部、烹调保留率、达标阈值、供能系数、试算、发布或回滚时使用。
---

# 童健云营养计算规则

本 Skill 把用户的业务描述转换为结构化规则变更。所有计算由童健云确定性规则引擎执行；不要自行估算数值，也不要生成或执行 Python、JavaScript、SQL 或 Shell 代码。

## 固定流程

1. 调用 `frappe_list_tongjianyun_nutrition_rules`，确认当前生效版本和已有草稿。
2. 将用户需求整理为 `changes` 对象；只使用参考契约允许的字段和公式语法。
3. 在创建前向用户复述：修改的营养指标、原规则、候选规则、变更理由和预期影响。缺少关键口径时先询问，不猜测医学或法规标准。
4. 用户确认草稿内容后，调用 `frappe_create_tongjianyun_nutrition_rule_draft`。创建草稿不影响线上结果。
5. 调用 `frappe_preview_tongjianyun_nutrition_rule`，至少选择一份真实食谱进行新旧对比；报告每个受影响指标的原值、新值、变化率和评价变化。
6. 只有试算无错误且用户明确要求送审时，调用 `frappe_submit_tongjianyun_nutrition_rule`。
7. 发布前再次说明规则编号、试算食谱、主要变化和生效影响。只有用户在当前对话明确确认发布该版本时，调用 `frappe_publish_tongjianyun_nutrition_rule`，并传 `confirmation="确认发布"`。
8. 回滚同样必须先说明目标历史版本和影响，用户明确确认后才调用 `frappe_rollback_tongjianyun_nutrition_rule`，并传 `confirmation="确认回滚"`。

## 安全边界

- 不修改已发布版本；任何变化都创建新版本。
- 不绕过 Frappe 角色权限。营养人员可创建、试算、送审；只有童健云管理员或系统管理员可发布和回滚。
- 不把模型输出当作已验证营养标准。法规、指南、论文或专家要求必须记录在 `source` 与 `change_reason` 中。
- 不在没有真实食谱试算的情况下送审或发布。
- 不使用通用 `frappe_create_document` 或 `frappe_update_document` 写规则 DocType。
- 公式不能包含属性访问、索引、条件表达式、循环、导入或任何非白名单函数。

## 完成标准

最终回复必须列出规则编号、版本、状态、规则指纹、试算食谱和主要差异。若未发布，明确写“线上规则未改变”；若已发布，必须回读并报告当前生效版本。

详细字段与公式语法见 `references/rule-contract.md`。
