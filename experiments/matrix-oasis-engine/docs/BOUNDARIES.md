# 模块边界

## 允许范围

- 文件变更：仅 `experiments/matrix-oasis-engine/**`。
- npm workspace：仅模块根声明并解析的内部 workspace。
- Creator 网络：无。
- 验证网络：仅本机 loopback，用于启动并请求自身 preview。
- 工具：Node 24.x、npm 11.x、Git；Godot 4.6.x 只作为未来可选诊断。
- R1 允许范围不包含 `apps/creator-web/**`；该目录是冻结的 R0 验收面。

## 禁止范围

- 导入、复制或运行父 `client/`、`server/`、根脚本、根配置或父构建产物。
- 修改父路由、API、数据库、环境变量、Docker、CI、公共类型或 Matrix Oasis 占位页。
- 使用模块外 `file:` / `link:` 依赖、绝对本机路径、逃逸相对路径或外部符号链接。
- 在 Creator 源码中使用 `fetch`、`XMLHttpRequest`、`WebSocket`、`EventSource`、环境变量或持久化 API。
- 跟踪 `.env`、密钥、日志、`node_modules`、`dist`、coverage、缓存、Godot 生成物或导出物。

## 父项目工具箱策略

父项目现有模块目前没有任何可调用白名单。若后续需要复用能力，只能在独立轮次提出版本化适配器设计并完成人工审批；不能以直接源码导入或共享依赖代替接口。R1 不选择传输方式、协议或数据格式。

任何提案都必须说明精确父文件、必要性、替代方案、影响、验收和回退。未批准时 `allowedParentInteractions` 必须保持空数组。

## 自动检查

`module-boundary.json` schema v2 是当前轮次机器策略源，固定 `activeRound=R1` 和 R1 基线。现有自动护栏包括：

- 环境 doctor；
- import、路径、依赖、网络、密钥和生成物护栏；
- 正向 fixture，以及 client/server 逃逸、外部依赖、外部符号链接、跨平台绝对路径、Creator 网络、密钥、环境文件和跟踪生成物负向 fixtures；
- 稳定的 `npm run verify` 聚合门。

文档允许出现父路径作为规则说明；扫描器只把可执行源码、manifest 和配置视为运行依赖证据。

`npm run verify` 的固定顺序为：doctor → R1 范围 → 当前模块边界 → Node 测试 → Creator 构建 → Creator loopback 冒烟。普通 doctor 中 Godot 4.6.x 是 warning；`npm run doctor:godot` 才是后续轮次的严格非零检查。

`npm run verify:extraction` 不属于 `verify`，因此不会递归。它从干净 HEAD 创建本地 clone，通过 `git subtree split` 保留模块历史，再在独立仓库根执行相同验证；临时仓库、archive 和详细日志始终位于仓外临时目录。

`npm run check:parent-scope -- --base <SHA>` 拒绝模块外变更；`npm run check:round-scope` 进一步拒绝 Creator 与模块外的 committed、staged、unstaged、untracked 变更。二者固定 R1 基线并使用 NUL 分隔 Git 输出。standalone 中 round scope 只在精确识别模块即仓库根时返回 `not_applicable`，其他错误均失败关闭。

## 回退

本轮没有父项目集成或数据迁移。逆序 revert R1 提交，或合并后 revert R1 PR，即可回到 R0；原 `/matrix-oasis` 页面不受影响。

## 并行工作树与共享栈

- R1 不重建或复用共享栈容器。
- 任何未来共享栈重建必须先由用户确认时间窗口和共享基线。
- 并行分支发生冲突时，先在模块独立 preview 验收；通过后仍要核对主线基线与完整 diff，再申请 PR。
