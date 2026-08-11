---
name: tongjianyun
description: 童健云业务专用操作规范。处理健康数据、膳食营养、就餐考勤、食品采购与食品安全追溯时使用。
---

# 童健云

本 Skill 专门负责童健云应用。跨应用请求由 `child-site` 协调，本 Skill 只处理其中的童健云部分。

## 可处理的业务

- 健康数据：儿童健康数据记录，不包含儿童主数据和班级维护。
- 膳食营养：食谱、菜品、食材、营养标准、膳食营养分析、就餐考勤。
- 食品安全：供应商、食品采购、留样、追溯事件、食材规格、食品分类与映射。

## 允许的数据对象

只允许访问名称以 `Tongjianyun` 开头的 DocType。主要对象包括：

- `Tongjianyun Data Record`
- `Tongjianyun Dish Catalog`
- `Tongjianyun Food Category`
- `Tongjianyun Food Category Mapping`
- `Tongjianyun Food Purchase`
- `Tongjianyun Food Sample`
- `Tongjianyun Food Supplier`
- `Tongjianyun Food Trace Event`
- `Tongjianyun Ingredient Spec`
- `Tongjianyun Meal Attendance`
- `Tongjianyun Meal Nutrition`
- `Tongjianyun Nutrition Standard`
- `Tongjianyun Recipe`
- `Tongjianyun Recipe Dish`
- `Tongjianyun Recipe Ingredient`

## 操作规则

1. 新建或修改前，先读取目标 DocType 的元数据，确认字段名、必填项和关联关系。
2. 不访问、创建或修改 `Tongjianyun Child` 和 `Tongjianyun Class`；涉及其他现有对象时先查询，避免重复创建供应商、食材或食谱。
3. 创建或修改后立即回读，向用户报告中文业务名称和记录编号。
4. 只创建或修改草稿；不声称已提交、删除、审批或完成系统没有开放的操作。
5. 请求同时涉及其他应用时，把其他部分交给匹配的业务 Skill，不把 ERPNext、Education 或其他应用对象误认为童健云数据。
6. 用户询问童健云能力时，只介绍本文件“可处理的业务”，不要列出通用 Shell 或文件系统能力。
