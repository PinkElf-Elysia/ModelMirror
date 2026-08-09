# Godot MCP 资格验证

R4 只评估 `@satelliteoflove/godot-mcp@4.1.0` 与 `@ryanmazzolini/minimal-godot-mcp@0.1.6`，不接入正式工程。

验证必须在仓外一次性工程副本执行，限定 loopback、无凭据，只做启动、握手、工具枚举、只读工程观察/诊断和进程清理。运行前后文件树必须一致。结果记录为 `recommended`、`rejected` 或 `deferred`，不影响 Godot 基础工程的自动验证结论。

任何 MCP addon、配置、包依赖或自动写入能力若需进入仓库，必须在后续轮次单独审批。
