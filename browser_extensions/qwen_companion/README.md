# 童健云智能助手 2.1.4

基于 2.1.3，仅新增侧边栏两个精确隐藏目标。

## 侧边栏

只检查：

- `div.sidebar-entry-list-content`
- 直接子元素 `div.sidebar-entry-list-text`

文本严格等于以下任一值时才隐藏该菜单项本身：

- `社区`
- `Coder`

命中后增加：

`data-ione-v214-sidebar-hidden="<文本>"`

CSS 只对带此专用标记的 `.sidebar-entry-list-content` 使用 `display:none`。

不会隐藏 `.sidebar-entry-list` 父容器，不影响新建对话、历史对话或其它侧边栏入口。

## 保留功能

- 精确隐藏真实 `qwen-logo.svg`
- 精确隐藏 `Qwen3.8-Max` 模型选择器
- 处理 Qwen SPA 的新增元素、文本节点和 characterData 变化
- 保留旧版本副作用恢复逻辑
