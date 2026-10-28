# Vega 研发平台 runner

本机由 Vega 平台调度：只执行下发任务要求的事，不扩大范围。

## 代码导航：优先原生 LSP

- 定义 / 引用 / 类型 / 文件符号 / 工程符号 → 原生 LSP 工具（Python、TypeScript、Java 已装）。
- 没有对应 LSP server 的语言直接降级到检索或读文件，不要空等。
- LSP 只读；跨文件改名与整段替换用 serena。

## 跨文件结构与检索

- code-review-graph MCP：任务开始先 get_minimal_context_tool(task=...)；影响面 get_impact_radius_tool；review 用 detect_changes_tool / get_review_context_tool。空结果只代表未索引，不等于不存在。
- serena MCP：符号级编辑、跨文件重命名（replace_symbol_body / rename_symbol）。
- ast-grep：跨文件结构化搜索与批量改写；先不带 --rewrite 看命中，确认后再改。
- 纯文本、配置、日志：用普通检索（Grep）。

## 事实优先级

- 源码高于一切索引；图谱、wiki、记忆都可能过期。
- 按行编辑、审阅未提交改动一律读工作区；不得凭图谱输出直接改代码。
- 改行为、迁移、重试、兜底、兼容逻辑前，先读实现与测试。

## 输出

- 不确定就说不确定，不要编造；结论先行，省略过程性描述。
