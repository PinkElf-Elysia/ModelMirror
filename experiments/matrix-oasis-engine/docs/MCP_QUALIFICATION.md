# Godot MCP 资格验证

R4 只评估 `@satelliteoflove/godot-mcp@4.1.0` 与 `@ryanmazzolini/minimal-godot-mcp@0.1.6`，不接入正式工程。

验证必须在仓外一次性工程副本执行，限定 loopback、无凭据，只做启动、握手、工具枚举、只读工程观察/诊断和进程清理。运行前后文件树必须一致。结果记录为 `recommended`、`rejected` 或 `deferred`，不影响 Godot 基础工程的自动验证结论。

任何 MCP addon、配置、包依赖或自动写入能力若需进入仓库，必须在后续轮次单独审批。

## R4.4 仓外结果

验证在一次性项目副本中使用 Godot `4.6.3`、Node `24.18.0` 执行。MCP 子进程只获得系统运行所需的白名单环境变量，没有凭据；编辑器桥接和 LSP 只使用默认 loopback。插件安装、npm 依赖、项目缓存和报告全部留在 `C:\tmp`，正式工程零改动。

| 候选 | 固定版本 | 结果 | 证据与边界 |
| --- | --- | --- | --- |
| `@ryanmazzolini/minimal-godot-mcp` | `0.1.6` / MIT | 推荐作为后续只读诊断候选 | npm integrity 匹配；stdio 握手、4 个工具枚举和 `scan_workspace_diagnostics` 真实 LSP 调用通过。 |
| `@satelliteoflove/godot-mcp` | `4.1.0` / MIT | 延后 | npm integrity 匹配；`--read-only` 启动、stdio 握手和 12 个只读工具枚举通过，但本轮 headless 编辑器会话中的 `godot_project get_info` 未建立可用桥接，不能宣称项目观察通过。 |

两项测试前后项目源树均为 `sha256:425c112fb7a35db1e134189dc32f7dcbeda8b60ac17204a8920479af1fa0014a`，所有由资格脚本创建的 MCP 与 Godot 进程均确认退出。完整 JSON 报告保存在仓外，仓内不保存插件、MCP 配置、依赖或动态日志。

“推荐”仅表示通过 R4 仓外只读资格，不代表批准集成；“延后”也不构成 Godot 工程自动门失败。若后续希望接入任一候选，必须单独评审其写能力、端口、依赖、更新策略和回退路径。
