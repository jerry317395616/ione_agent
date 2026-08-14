# 工具契约

## `frappe_stage_spreadsheet_attachment`

输入父文档 `doctype`、`document_name` 和精确 `file_name`。工具使用当前登录用户权限读取附件，验证 `.xlsx` 包并把文件写入受限工作目录。只使用返回的相对 `local_path`；不要猜测服务器路径。

## `officecli_xlsx`

输入一个 `command` 字符串，不包含 `officecli` 前缀。开放的动词包括 `help`、`load_skill`、`create`、`open`、`save`、`close`、`view`、`get`、`query`、`add`、`set`、`remove`、`move`、`swap`、`batch`、`import`、`dump` 和 `validate`。所有文件参数必须是工作目录相对路径。

不熟悉某个元素时，先运行：

```text
help xlsx
help xlsx add chart
help xlsx set cell
```

工作簿的最终最低验证：

```text
validate spreadsheets/<file>.xlsx
view spreadsheets/<file>.xlsx issues
```

## `frappe_publish_spreadsheet_attachment`

输入父文档、stage 返回的 `local_path` 和可选的新 `file_name`。工具再次验证文件，通过当前登录用户写权限上传为私有附件。成功结果包含 Frappe 文件 URL。

## 安全边界

- 不向用户或模型展示 Base64、认证令牌、绝对路径或部署信息。
- 不接受绝对路径、`..`、宏工作簿或非 `.xlsx` 文件。
- 不调用 OfficeCLI 的安装、插件、MCP、watch 或其他开放式命令。
- 原附件保留；用户明确要求覆盖时也应优先生成可回滚的新版本。
