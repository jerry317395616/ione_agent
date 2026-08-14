---
name: frappe-spreadsheets
description: Edit, analyze, extend, validate, and return real .xlsx workbooks attached to Frappe documents. Use when a user asks I-ONE Agent to inspect an Excel attachment, add or redesign sheets, add formulas/charts/pivots, preserve a template, perform free-form analysis, or provide a modified workbook for download. Use this instead of a fixed report generator when the request changes an existing workbook or asks for custom spreadsheet design.
---

# Frappe 电子表格

把 Frappe 私有 Excel 附件安全暂存到站点工作目录，用 OfficeCLI 按用户要求自由编辑，校验后作为新的私有 `.xlsx` 附件回传。文件字节不得进入模型上下文。

## 必须遵循的流程

1. 确定唯一的父文档和原始附件。必要时先用 `frappe_list_documents`、`frappe_get_document`、`frappe_list_attachments`；有歧义就列出候选，不猜。
2. 调用 `frappe_stage_spreadsheet_attachment`，只使用其返回的 `local_path`。
3. 调用 `officecli_xlsx`：先 `view <local_path> outline`，再按需用 `view ... text`、`get` 或 `query` 检查数据和现有格式。
4. 按用户目标设计修改。需要命令语法时先执行 `help xlsx ...`；复杂修改优先使用一条 `batch` 命令，避免逐单元格消耗工具轮次。
5. 至少执行 `validate <local_path>` 和 `view <local_path> issues`。若有公式错误、断裂引用或明显排版问题，修复后再复验。
6. 调用 `frappe_publish_spreadsheet_attachment`，使用清晰的新文件名，默认保留原附件。
7. 最终回复必须给出新附件名称和工具返回的 `file`/`file_url`，并简述新增或修改的 Sheet。

## 编辑规则

- 用户说“增加一个 Sheet”“再分析一些数据”时，分析维度和 Sheet 布局应依据当前工作簿内容与用户问题自由设计，不能退回固定食谱报告接口。
- 修改现有模板时保留原有 Sheet、样式、合并单元格、列宽、打印设置和公式约定；新增 Sheet 应匹配原工作簿的视觉语言。
- 能由原始数据推导的结果使用公式，不硬编码计算结果。不得捏造缺失数据；需要外部数据时说明来源或向用户确认。
- 交付物必须是真实 `.xlsx`。禁止因为上传不便而降级为 `.txt`、`.csv`、JSON 或“多 Sheet 数据说明”。
- 不覆盖 Frappe 原附件。推荐名称：`原文件名-自定义分析-YYYYMMDD.xlsx`；同一会话再次修改时可在文件名中增加版本号。
- 只处理 `.xlsx`，不接受 `.xlsm`，不保留宏或嵌入对象。

## OfficeCLI 示例

传给 `officecli_xlsx.command` 的内容不要包含 `officecli` 前缀：

```text
view spreadsheets/食谱带量分析.xlsx outline
view spreadsheets/食谱带量分析.xlsx text --start 1 --end 60
add spreadsheets/食谱带量分析.xlsx / --type sheet --prop name=补充分析
validate spreadsheets/食谱带量分析.xlsx
view spreadsheets/食谱带量分析.xlsx issues
```

批量写入时把 JSON 作为完整的 `--commands` 参数传入；每个操作使用 OfficeCLI 的 `command/path/props` 结构。属性不确定时先调用 `help xlsx <verb> <element>`，已安装版本的帮助是唯一权威。

## 浏览器模型约束

每轮只能调用一个工具。尽量使用下面的闭环：定位附件 → stage → 读取 → batch 修改 → validate/issues → publish。若需要额外检查，优先把修改合并为 batch。

详细工具契约见 [references/tool-contract.md](references/tool-contract.md)。
