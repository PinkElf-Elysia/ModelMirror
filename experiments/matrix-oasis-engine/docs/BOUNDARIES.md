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

现有 Authoring Pack CLI 和 Creator 本地入口继续执行路径/realpath、`.json`、1 MiB、fatal UTF-8 与安全诊断检查。R3 Runtime Validator 只接受调用方已提供的两个 JSON 字符串，不读取文件；先验证 Pack/Receipt 合同与 typed index，再强制规范文本、UTF-8 byteLength 和 Web Crypto SHA-256。

R3.3 Compiler 运行源码保持浏览器兼容，只通过冻结包根 API 读取已捕获快照；Node 文件能力仅存在于模块根 CLI。编译 CLI 只接受模块内相对 `.json`、1 MiB 与安全小写 slug；使用 bigint 文件身份、fatal UTF-8、同父目录暂存、`wx+` 独占 FileHandle、句柄回读自校验及单次目录 rename 发布固定文件对。已存在目录、外部 junction、读取期间替换与可观察竞态均失败关闭，清理只处理能证明仍是本次创建身份的暂存目录；身份不可信的最终目标不递归删除。Runtime 回验 CLI 对 Pack/Receipt 分别限制为 16 MiB/16 KiB，并复用公开 Validator。`exports/` 始终忽略且不得跟踪。

Node 24 没有可移植的 `openat`/目录句柄相对打开接口，因此同用户恶意宿主仍可在“身份门→open 系统调用”的不可原子瞬间制造外部零字节文件，或在最终回验返回后再次篡改 Artifact。R3.3 保证在句柄身份验证前不写入 Pack 内容、不会把可观察替换宣称成功；不把此边界描述成对恶意宿主或任意文件系统的安全事务。

R2 Simulator 只能通过包根公开 API 作为黑盒 oracle。R3 contracts/validator 运行源码保持浏览器兼容，不访问网络、环境变量、文件系统、storage 或 `node:*`；Node 文件入口只能存在于模块根 CLI/验证层。

R3.4 Runtime Simulator 只接受规范 Runtime Pack JSON 与必需 Receipt JSON；准备阶段先调用公开 Runtime Validator，再建立 opaque handle。运行快照使用索引位置与变量数组，并绑定 source SHA-256 和 artifact SHA-256；它只用于当前 prepared handle 的内存实验，不是正式存档。Parity harness 分别调用冻结 R2 包根与独立 Runtime 包根，排除有意不同的索引/哈希身份后比较全部可观察结果；任何单侧失败或投影差异只返回静态 parity 诊断，不发布候选快照。两个新包运行源码同样禁止网络、环境、文件系统、storage、父源码与 `node:*`。

R3.5 Creator 只依赖 parity harness 包根。内置与本地 Pack 都先完成编译、双侧 prepare/create，再形成独立候选；异步候选、重置和单步用捕获会话引用做最终 CAS，迟到或失败结果不能覆盖新状态。Creator 只在用户点击下载按钮后，以内存 `Blob` 和临时 object URL 输出当前规范 Runtime Pack 或 Receipt；object URL 在触发后立即撤销。该动作不访问网络、不调用 File System API、不写 storage、不自动保存，也不改变当前会话。

## 自动范围门

- schema v3 固定 `activeRound=R3` 与基线 `380c747e62193855c724a947d99a84070ca623ff`。
- `check:round-scope` 同时校验 JSON 与代码中的有序文件表、包前缀和冻结路径。
- `check:parent-scope -- --base <SHA>` 拒绝全部模块外变化；调用者不能用较新 SHA 缩短检查范围。
- 两个检查均解析 NUL 分隔 Git 输出；未知或异常状态失败关闭。
- standalone 中 round scope 只在模块精确等于仓库根时返回 `not_applicable`。
- `verify` 执行 doctor、R3 scope、boundary、冻结 Pack 验证、Runtime Pack、Compiler、独立 Runtime Simulator、parity、全量 Node tests、Creator build 与 loopback smoke。

## 父项目、共享栈与回退

`parentIntegration` 保持 `none`，`allowedParentInteractions` 为空。父项目能力只能在未来独立轮次通过版本化适配器并经人工审批后使用。

R3 不重建或复用共享栈；任何例外必须先确认时间窗口和共享基线。每批可逆序 `git revert`，整体回退 R3 PR 后恢复 R2；没有数据库、服务或运行数据需要迁移。
