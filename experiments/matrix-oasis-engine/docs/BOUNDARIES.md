# 模块边界

## 允许范围

- 文件变更：仅 `experiments/matrix-oasis-engine/**`。
- npm workspace：仅模块根声明并解析的内部 workspace。
- Creator 网络：无。
- 验证网络：仅本机 loopback，用于启动并请求自身 preview。
- 工具：Node 24.x、npm 11.x、Git；Godot 4.6.x 只作为未来可选诊断。

## 禁止范围

- 导入、复制或运行父 `client/`、`server/`、根脚本、根配置或父构建产物。
- 修改父路由、API、数据库、环境变量、Docker、CI、公共类型或 Matrix Oasis 占位页。
- 使用模块外 `file:` / `link:` 依赖、绝对本机路径、逃逸相对路径或外部符号链接。
- 在 Creator 源码中使用 `fetch`、`XMLHttpRequest`、`WebSocket`、`EventSource`、环境变量或持久化 API。
- 跟踪 `.env`、密钥、日志、`node_modules`、`dist`、coverage、缓存、Godot 生成物或导出物。

## 父项目工具箱策略

父项目现有模块目前没有任何可调用白名单。若后续需要复用能力，只能在独立轮次提出版本化适配器设计并完成人工审批；不能以直接源码导入或共享依赖代替接口。R0 不选择传输方式、协议或数据格式。

任何提案都必须说明精确父文件、必要性、替代方案、影响、验收和回退。未批准时 `allowedParentInteractions` 必须保持空数组。

## 自动检查

`module-boundary.json` 是 R0 机器可读策略源。R0.3 将提供：

- 环境 doctor；
- import、路径、依赖、网络、密钥和生成物护栏；
- 正向与负向 fixtures；
- 稳定的 `npm run verify` 聚合门。

文档允许出现父路径作为规则说明；扫描器只把可执行源码、manifest 和配置视为运行依赖证据。

## 回退

本轮没有父项目集成或数据迁移。逆序 revert 模块提交，或在合并后 revert 整个 R0 PR，即可移除全部变更；原 `/matrix-oasis` 页面不受影响。

## 并行工作树与共享栈

- R0 不重建或复用共享栈容器。
- 任何未来共享栈重建必须先由用户确认时间窗口和共享基线。
- 并行分支发生冲突时，先在模块独立 preview 验收；通过后仍要核对主线基线与完整 diff，再申请 PR。
