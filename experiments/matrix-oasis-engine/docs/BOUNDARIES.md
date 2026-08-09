# 模块边界

## R3 允许范围

- 仅修改 `experiments/matrix-oasis-engine/**`。
- npm workspace 仅解析模块根声明的内部 workspace。
- Creator 不访问网络；验证网络仅限自身 loopback preview。
- Node 24.x、npm 11.x、Git 是必需工具；Godot 4.6.x 仍是未来可选诊断。
- `module-boundary.json` schema v3 的 `allowedModuleFiles` 对既有 app/docs/scripts/tests 逐文件放行。
- `allowedModulePrefixes` 只放行五个新包：Compiler、parity harness、Runtime Pack contracts/validator/simulator。
- 精确文件与前缀之外的模块路径失败关闭，不能依赖目录归属获得隐式许可。

## 冻结权威

- R1：Authoring contract 文档、contracts/validator workspace、Validator CLI 与测试、两个 examples。
- R2：game-pack-simulator workspace 与语义测试。
- 历史：ADR-0001 至 ADR-0003、R0 至 R2 验收记录。

冻结路径优先于 allowlist；任何 committed、staged、unstaged 或 untracked 变化都返回通用 `ROUND_GUARD_FROZEN_ARTIFACT_CHANGED` 并阻断本轮。

## 禁止范围

- 导入、复制或运行父 `client/`、`server/`、根配置、父构建产物或依赖目录。
- 修改父路由、API、数据库、环境变量、Docker、CI、公共类型或 Matrix Oasis 占位页。
- 使用模块外 `file:` / `link:`、绝对路径、目录穿越或外部符号链接。
- 在 Creator 中使用网络、环境变量或持久化 API。
- 从 R2 Simulator 的 `src/**` 导入、抽取 evaluator 或共享 condition/effect 执行核。
- 在诊断中回显输入值、未知键名、绝对路径、底层异常或堆栈。
- 跟踪密钥、环境文件、日志、依赖、构建、coverage、cache、exports、Godot 或二进制产物。

## 输入与工具边界

现有 Authoring Pack CLI 和 Creator 本地入口继续执行路径/realpath、`.json`、1 MiB、fatal UTF-8 与安全诊断检查。R3 Compiler/Runtime CLI 的新上限、输出和完整性规则必须在对应批次实现后才能宣称可用；R3.1 不新增文件入口。

R2 Simulator 只能通过包根公开 API 作为黑盒 oracle。R3 新包必须浏览器兼容，运行源码不得访问网络、环境变量、文件系统或 `node:*`；Node 文件入口只能存在于模块根 CLI/验证层。

## 自动范围门

- schema v3 固定 `activeRound=R3` 与基线 `380c747e62193855c724a947d99a84070ca623ff`。
- `check:round-scope` 同时校验 JSON 与代码中的有序文件表、包前缀和冻结路径。
- `check:parent-scope -- --base <SHA>` 拒绝全部模块外变化；调用者不能用较新 SHA 缩短检查范围。
- 两个检查均解析 NUL 分隔 Git 输出；未知或异常状态失败关闭。
- standalone 中 round scope 只在模块精确等于仓库根时返回 `not_applicable`。
- `verify` 继续执行 doctor、R3 scope、boundary、冻结 Pack 验证、Node tests、Creator build 与 loopback smoke。

## 父项目、共享栈与回退

`parentIntegration` 保持 `none`，`allowedParentInteractions` 为空。父项目能力只能在未来独立轮次通过版本化适配器并经人工审批后使用。

R3 不重建或复用共享栈；任何例外必须先确认时间窗口和共享基线。每批可逆序 `git revert`，整体回退 R3 PR 后恢复 R2；没有数据库、服务或运行数据需要迁移。
