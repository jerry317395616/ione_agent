# 童健云已有食谱分析契约

## 目标记录查询

使用 `frappe_list_documents` 查询：

```json
{
  "doctype": "Tongjianyun Recipe",
  "filters": {"is_deleted": 0},
  "fields": [
    "name",
    "recipe_id",
    "title",
    "week_start",
    "week_end",
    "workflow_status",
    "is_deleted",
    "modified"
  ],
  "order_by": "week_start desc",
  "limit": 20
}
```

日期范围明确时，在 `filters` 中加入 `week_start`、`week_end`。记录较多时使用 `has_more` 和 `next_start` 分页，不要把未取到的记录判定为不存在。

使用 `frappe_get_document` 最终确认：

```json
{
  "doctype": "Tongjianyun Recipe",
  "name": "2026-W17"
}
```

## 报告生成

唯一确认记录后调用：

```json
{
  "recipe_name": "2026-W17"
}
```

工具：`frappe_generate_tongjianyun_recipe_analysis`

服务端成功结果包含：

- `recipe`：实际分析的食谱内部编号；
- `file_name`：生成的 Excel 文件名；
- `download_url`：登录用户可下载的私有文件地址；
- `file_size`：文件大小；
- `ingredient_count`：实际进入分析的食材数；
- `analysis.profile`：采用的营养标准画像；
- `analysis.conclusion`：确定性分析结论。

## 质量要求

- 报告内容完全来自已保存食谱的菜品、食材、带量和就餐数据。
- 食物名称是动态的，不能使用固定示例或预置菜名替换。
- 下载链接必须直接取自工具返回值，不能自行拼接域名或文件路径。
- 分析报告是私有附件，仍受当前登录用户权限控制。
