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

## 完整食谱保存

- 用户上传 Excel、CSV 食谱时，不要把整份附件交给模型自行拼装 JSON。桥接服务会先创建持久化食谱导入任务，确定性识别标题、日期、餐次、菜品、带量和食材关系，并返回预览、置信度和问题清单。
- 上传后先向用户展示解析统计。存在错误时不得写入；存在菜品与食材关系警告时，必须等待用户明确回复“确认按当前解析结果录入食谱”。
- 用户后续回复“录入食谱”时，要继续使用当前对话中最新的导入任务，不要再次索要已经上传的附件或食谱数据。
- 创建或替换一周完整食谱时，必须使用 `frappe_upsert_tongjianyun_recipe`，不要分别调用通用创建工具写入 `Tongjianyun Recipe Dish` 或 `Tongjianyun Recipe Ingredient`。
- `recipe` 必须包含 `recipeId`、`title`，并按已有数据填写 `weekStart`、`weekEnd`、`sourceFileName`、`parser`、`relationSource` 等来源信息。
- `days` 中每一天使用 `id`、`date`、`day` 和 `portions`；每个餐次使用 `slot`、`label`、`dishes`、`amountPerChild`、`totalAmount` 和 `dishIngredientRows`。
- 每条 `dishIngredientRows` 使用 `dishName`、`ingredient`、`amount`、`unit`。不要提供 `dish_row_id` 或 `ingredient_row_id`，这两个字段由服务器生成。
- 同一 `recipeId` 再次保存会原子替换该食谱的菜品和食材明细。调用前先查询是否已有记录，并在结果中明确报告是新建还是更新，以及保存后的天数、菜品数和食材数。
