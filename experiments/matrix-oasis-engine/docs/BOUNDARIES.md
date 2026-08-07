# 模块边界

## 允许范围

- 文件变更：仅 `experiments/matrix-oasis-engine/**`。
- npm workspace：仅模块根声明并解析的内部 workspace。
- Creator 网络：无。
- 验证网络：仅本机 loopback，用于启动并请求自身 preview。
- 工具：Node 24.x、npm 11.x、Git；Godot 4.6.x 只作为未来可选诊断。
- R2 正向 allowlist 只包含指定模块根文件、`apps/creator-web/**`、`packages/game-pack-simulator/**`、`scripts/**`、`tests/**` 与非冻结 `docs/**`。
- `packages/game-pack-contracts/**`、`packages/game-pack-validator/**`、`examples/**`、R1 权威合同说明、R0/R1 历史 ADR 和验收记录冻结规则优先于 allowlist。

## 禁止范围

- 导入、复制或运行父 `client/`、`server/`、根脚本、根配置或父构建产物。
- 修改父路由、API、数据库、环境变量、Docker、CI、公共类型或 Matrix Oasis 占位页。
- 使用模块外 `file:` / `link:` 依赖、绝对本机路径、逃逸相对路径或外部符号链接。
- 在 Creator 源码中使用 `fetch`、`XMLHttpRequest`、`WebSocket`、`EventSource`、环境变量或持久化 API。
- 将浏览器选择的本地 Pack 上传、保存、记录文件名或传给父项目；本地入口只在内存读取单个 `.json`。
- 让 Pack CLI 接受模块外、绝对、含 `..`、符号链接逃逸或真实扩展名非 `.json` 的输入；CLI 只读不超过 1 MiB 的模块内 UTF-8 JSON。
- 在验证报告中回显 Pack 值、任意未知键名、Ajv 参数、文件绝对路径、底层异常或堆栈。
- 跟踪 `.env`、密钥、日志、`node_modules`、`dist`、coverage、缓存、Godot 生成物或导出物。

## 父项目工具箱策略

父项目现有模块目前没有任何可调用白名单。若后续需要复用能力，只能在独立轮次提出版本化适配器设计并完成人工审批；不能以直接源码导入或共享依赖代替接口。R2 不选择传输方式、协议或数据格式。

任何提案都必须说明精确父文件、必要性、替代方案、影响、验收和回退。未批准时 `allowedParentInteractions` 必须保持空数组。

## 自动检查

`module-boundary.json` schema v2 是当前轮次机器策略源，固定 `activeRound=R2`、R2 基线与有序的正向 allowlist/冻结路径。现有自动护栏包括：

- 环境 doctor；
- import、路径、依赖、网络、密钥和生成物护栏；
- 正向 fixture，以及 client/server 逃逸、外部依赖、外部符号链接、跨平台绝对路径、Creator 网络、密钥、环境文件和跟踪生成物负向 fixtures；
- 稳定的 `npm run verify` 聚合门。

Pack Validator 自身无文件、网络或环境访问；模块根 CLI 是唯一文件入口，并同时执行词法 containment、realpath containment、真实扩展名、文件类型、读前/读后大小与 fatal UTF-8 检查。内容诊断退出 1，路径或工具故障退出 2；机器 JSON 模式只输出白名单化报告字段。

Creator 的浏览器本地入口独立执行大小写不敏感 `.json` 检查、读前与读后 1 MiB 上限、`arrayBuffer()` 实际长度复核和 fatal UTF-8 解码。每次异步读取使用递增 token；只有最新候选完整通过 Validator 与会话创建后才返回冻结 candidate。拒绝或过期结果保持原会话引用，不保存或回显本地文件名。

Creator 的 reset 与单步 action 必须基于同一 active session 计算完整候选，再以引用 CAS 提交；迟到候选不得覆盖更新后的会话，也不得把新 prepared 与旧 snapshot 拼接。模拟器抛出的 operational error 只映射为静态 `PACK_RUNTIME_INTERNAL_ERROR`，不进入控制台或用户诊断的底层异常文本。

文档允许出现父路径作为规则说明；扫描器只把可执行源码、manifest 和配置视为运行依赖证据。

`npm run verify` 的固定顺序为：doctor → R2 范围 → 当前模块边界 → Pack 样例验证 → Node 测试 → Creator 构建 → Creator loopback 冒烟。普通 doctor 中 Godot 4.6.x 是 warning；`npm run doctor:godot` 才是后续轮次的严格非零检查。

`npm run verify:extraction` 不属于 `verify`，因此不会递归。它从干净 HEAD 创建本地 clone，通过 `git subtree split` 保留模块历史，再在独立仓库根执行相同验证；临时仓库、archive 和详细日志始终位于仓外临时目录。

模块根 `.gitattributes` 将所有检测为文本的文件固定为 LF，并显式保持常见二进制资产和归档为 binary；父仓、subtree 与 standalone checkout 必须得到相同的样例和脚本字节。

`npm run check:parent-scope -- --base <SHA>` 拒绝模块外变更；`npm run check:round-scope` 对 committed、staged、unstaged、untracked 统一执行冻结路径优先的 R2 正向 allowlist，未知模块路径失败关闭。二者固定 R2 基线并使用 NUL 分隔 Git 输出。standalone 中 round scope 只在精确识别模块即仓库根时返回 `not_applicable`，其他错误均失败关闭。

## 回退

本轮没有父项目集成或数据迁移。逆序 revert R2 提交，或合并后 revert R2 PR，即可回到 R1；原 `/matrix-oasis` 页面不受影响。

## 并行工作树与共享栈

- R2 不重建或复用共享栈容器。
- 任何未来共享栈重建必须先由用户确认时间窗口和共享基线。
- 并行分支发生冲突时，先在模块独立 preview 验收；通过后仍要核对主线基线与完整 diff，再申请 PR。
