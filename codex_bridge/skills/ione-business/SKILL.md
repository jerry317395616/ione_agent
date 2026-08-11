---
name: ione-business
description: I-ONE 用户侧业务数据规范。用于当前用户可见的经营目标、智能记录、证据、成果、经验、成长计划和评价等业务对象，不允许修改 Agent 自身配置或审计数据。
---

# I-ONE 业务数据

1. 使用 `frappe_get_site_catalog` 的 `app=ione_core` 或按名称搜索，确认当前用户实际可见的业务对象。
2. 可查询和草稿维护经营目标、智能记录、证据、成果、经验、成长计划、评价及其正常业务关联。
3. 写入前读取元数据并查询关联对象，创建后回读。
4. 不访问或修改 I-ONE Settings、Agent、Agent Tool、Agent Role、Channel、MCP Audit Log、AI Run Log、Flow Execution Policy、Publish Job 或其他运行时/配置/审计对象。
5. 不执行审批、发布、提交、取消、删除或外部动作；需要时只准备草稿并说明人工下一步。

