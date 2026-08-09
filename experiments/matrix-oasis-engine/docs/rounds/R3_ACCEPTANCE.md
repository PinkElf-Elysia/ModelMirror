# R3 验收记录

状态：R3.1-R3.5 已提交；R3.6 自动证据与人工验收包已收口，等待本地提交和用户最终验收

固定基线：`380c747e62193855c724a947d99a84070ca623ff`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [x] 全部 R3 变化严格位于模块目录并符合 schema v3 正向 allowlist。
- [x] R1/R2 冻结路径相对固定基线零差异。
- [x] Runtime Pack/Receipt 合同、Compiler、独立 Runtime Simulator 与 parity harness 按批次通过。
- [x] Creator 双执行实验台在合法、拒绝和竞态场景保持原子锁步。
- [x] 完整 verify、独立拆分与父前端无回归自动门通过。
- [x] 独立浏览器完成两个内置输入切换、单步、桌面/320px、焦点与控制台子集验收。
- [x] 父源码、Matrix Oasis 占位、配置和共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R3.1 | 治理、精确 allowlist 与 R1/R2 冻结 | `27a33d65e8eb7ed43821a907ae991797449ed5bc` | 已完成 |
| R3.2 | Runtime Pack/Receipt 合同与 Validator | `c019585420ca9e9c3d979a98767699a95e842586` | 已完成 |
| R3.2a | 冻结 R1 字符串域的规范化兼容修正 | `5af297be551f37bfba938bc927cd12d17350fc2b` | 已完成 |
| R3.3 | 确定性 Compiler 与安全 CLI | `a888cf5c9f1eea23de88f139bd8f105ee1a4b641` | 已完成 |
| R3.4 | 独立 Runtime Simulator 与 parity harness | `ace01117ef3a7075ef82ef36c840e71365b18967` | 已完成 |
| R3.5 | Creator 双执行锁步实验台 | `ee5d9544a749eddefd800138a9ecb89dd8421358` | 已完成 |
| R3.6 | 拆分、无回归、浏览器与证据收口 | 本批提交；最终 SHA 在仓外交付清单记录 | 已验证，等待本地提交 |

## R3.1 验收证据

变更严格限于 17 个模块内治理文件：

- 模块根：`AGENTS.md`、`README.md`、`module-boundary.json`、`package.json`、`package-lock.json`。
- 文档：`docs/ARCHITECTURE.md`、`docs/BOUNDARIES.md`、`docs/DEPENDENCIES_AND_LICENSES.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/RUNTIME_PACK_THREAT_MODEL.md`、`docs/adr/0004-r3-runtime-pack-governance.md`、本文。
- 护栏与测试：`scripts/check-round-scope.mjs`、`scripts/lib/boundary-core.mjs`、`scripts/lib/parent-scope-core.mjs`、`scripts/lib/scope-policy.mjs`、`tests/round-scope.test.mjs`。

已执行并通过：

- `npm.cmd ci --offline --no-audit --no-fund`：从本机缓存安装 79 packages，退出 0；未联网，lockfile 无额外变化。
- 定向 scope/boundary 测试：83/83 通过。
- `npm.cmd test`：221/221 通过。
- `npm.cmd run verify`：7/7 步通过；Creator 构建转换 227 modules；loopback smoke 返回 HTTP 200 并命中 R0/R2 稳定标识。
- `npm.cmd run check:round-scope`：17/17 路径通过。
- `npm.cmd run check:parent-scope -- --base 380c747e62193855c724a947d99a84070ca623ff`：17/17 路径通过。
- `npm.cmd run check:boundary`：checked 87、tracked 84、零违规。
- `npm.cmd prefix` 与 `npm.cmd ls --all`：退出 0。
- `git diff --check`：退出 0。

范围证据：R1/R2 冻结路径相对固定基线零差异；`client`、`server`、`.github`、Docker、根 manifest/lock 与现有 Matrix Oasis 文件零差异；无 staged 文件。

环境事实：首次测试因隔离 worktree 尚无 `node_modules` 出现 6 个 `ERR_MODULE_NOT_FOUND`，执行离线 `npm ci` 后消除。首次 Creator build 在沙箱内创建忽略的 `dist` 目录时遇到 `EPERM`；仅对模块本地 build/verify 使用提升后的文件写权限重跑即通过。未终止进程、未操作父仓或共享栈，故该事件不作为代码失败。

风险与回退：R3.1 仅改变治理、范围护栏、文档和模块根版本标识；逆序 `git revert` 本提交即可恢复 R2 治理。无数据库、服务、路由、环境变量或运行数据迁移。

## R3.2 验收证据

本批变更严格限于 30 个模块内路径：

- 模块根与文档：`README.md`、`package.json`、`package-lock.json`、`scripts/run-verify.mjs`、`docs/ARCHITECTURE.md`、`docs/BOUNDARIES.md`、`docs/DEPENDENCIES_AND_LICENSES.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/RUNTIME_GAME_PACK.md`、`docs/RUNTIME_PACK_THREAT_MODEL.md` 与本文。
- Runtime 合同包：`packages/runtime-pack-contracts/**` 共 8 个文件，包含两份权威 Schema、canonical-json/1 实现、类型、说明和测试。
- Runtime Validator：`packages/runtime-pack-validator/**` 共 11 个文件，包含公开入口、诊断、严格解析、结构/语义/完整性验证、类型、说明和测试。

已执行并通过：

- `npm.cmd ci --offline --no-audit --no-fund`：从本机缓存安装 81 packages，退出 0；未联网，未新增 registry 依赖。
- `npm.cmd prefix`：精确指向当前 R3 模块根；`npm.cmd ls --all`：退出 0，无 missing/extraneous，仅既有平台 optional dependency 与 esbuild install-script 提示。
- `npm.cmd run test:runtime-pack`：49/49 通过；覆盖合同闭合性、规范 JSON、双文档 parse/schema/semantic/integrity 门、typed index、图、Unicode/数字规范拼写、256/257 深度边界、深输入 fresh process、完整性与静态脱敏。
- `npm.cmd test`：270/270 通过，既有 R0-R2 harness 与语义测试无回归。
- 浏览器兼容 bundle：contracts 19,010 bytes、validator 345,326 bytes；`platform=browser`、无 `node:*`、文件系统、网络、环境变量或 storage 依赖。
- `npm.cmd run verify`：8/8 步通过；包含 doctor、round scope、boundary、样例、Runtime Pack、全部测试、Creator build 与 loopback smoke。
- `npm.cmd run check:boundary`、`npm.cmd run check:round-scope`、`npm.cmd run check:parent-scope -- --base 380c747e62193855c724a947d99a84070ca623ff` 与 `git diff --check`：全部退出 0。

范围证据：R1/R2 contracts、Validator、Simulator、examples、既有 CLI/语义测试及 R0-R2 ADR/验收记录相对固定基线零差异；父 `client`、`server`、`.github`、Docker、根 manifest/lock 与现有 Matrix Oasis 页面零差异。未启动父前后端、Docker 或共享栈。

安全事实：原始 JSON 嵌套在递归 parser 前以字符串感知扫描限制为 256 层；重复键仅在当前位置 Schema 声明属性时发布精确 pointer，错层或未知键退回安全父路径。Web Crypto、Proxy trap 与解析器故障只产生固定 operational code，不回显输入、键名、哈希或底层异常。

已知限制与回退：R3.2 尚无 Compiler、独立 Runtime Simulator、parity harness 或 Creator 双执行；`source.canonicalSha256` 只能校验格式，Receipt 不是签名或信任证明。逆序 `git revert` 本批提交可删除两个新 workspace、根脚本接线和本批文档，R1/R2 冻结输入与现有 Creator 保持不变；无数据库、服务、路由、环境变量或运行数据迁移。

## R3.2a 验收证据

R3.3 实施前发现冻结 R1 Validator 会合法接受孤立 UTF-16 代理项，而 R3.2 canonicalizer 会拒绝，导致“全部 R1 合法 Pack 均可编译”的退出条件无法成立。用户明确批准采用 R3 内兼容修正，不修改冻结 R1，也不把输入替换为 `U+FFFD`。

本修正严格限于 9 个模块内路径：canonicalizer 实现与测试、Runtime Validator 集成测试、两包 README、模块 README、`docs/RUNTIME_GAME_PACK.md`、`docs/RUNTIME_PACK_THREAT_MODEL.md` 与本文。Schema、格式版本、公开导出、依赖和 lockfile 均未改变。

固定行为与证据：

- 配对代理项继续编码为真实 Unicode；孤立高、低代理项及对象键由 ECMAScript well-formed `JSON.stringify` 固定为小写 `\uXXXX` ASCII 转义。
- 真实 `U+FFFD` 保持其 UTF-8 字符；孤立高代理项、孤立低代理项和 `U+FFFD` 的规范字节及 SHA-256 两两不同，不发生替换字符哈希碰撞。
- Runtime Validator 接受小写 escaped canonical 文本与匹配 Receipt；拒绝原始孤立代码单元和大写 `\uD800` 等非规范拼写。
- 冻结 R1 Validator 对带孤立代理项的 Authoring Pack 仍返回合法，且 R1 文件相对固定基线零差异。
- `npm.cmd run test:runtime-pack`：52/52 通过；`npm.cmd test`：273/273 通过。
- 浏览器兼容 bundle：contracts 18,497 bytes、validator 344,813 bytes；运行源码仍无 `node:*`、网络、文件系统、环境变量或 storage。
- `npm.cmd run verify`：8/8 步通过；scope、boundary、parent guard、UTF-8 与 `git diff --check` 全部退出 0。

回退：单独 revert 本修正提交即可恢复 R3.2 的严格 well-formed UTF-16 限制，不触碰 R1/R2、Schema、Receipt、Creator、父仓或共享栈。若回退，R3.3 的“全部 R1 合法 Pack 可编译”退出条件会再次被阻断。

## R3.3 验收证据

本批变更严格限于 22 个模块内路径：

- Compiler workspace：`packages/game-pack-compiler/**` 共 6 个文件，包含 package、公开实现/类型、说明和测试。
- Node CLI：`scripts/compile-pack.mjs`、`scripts/validate-runtime-pack.mjs`、`scripts/lib/runtime-pack-input-core.mjs`、`tests/compiler-cli.test.mjs`、`tests/runtime-pack-cli.test.mjs`。
- 模块根接线：`package.json`、`package-lock.json`、`scripts/run-verify.mjs`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`docs/BOUNDARIES.md`、`docs/DEPENDENCIES_AND_LICENSES.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/RUNTIME_GAME_PACK.md`、`docs/RUNTIME_PACK_THREAT_MODEL.md` 与本文。

已执行并通过：

- `npm.cmd ci --offline --no-audit --no-fund`：从本机缓存安装 82 packages，退出 0；未联网，lockfile 只新增内部 Compiler workspace link/元数据。
- `npm.cmd prefix`：精确指向当前 R3 模块根；`npm.cmd ls --all`：退出 0，无 missing/extraneous。
- Compiler package：20/20 通过；类型声明 strict 检查通过，且只从权威 contracts 进行 type-only import，不复制 Runtime 合同。
- `npm.cmd run test:compiler`：49/49 通过，无跳过；覆盖完整字段/union/index 映射、`-0`、20 次及并发确定性、Unicode/数字等价拼写、Web Crypto/自校验故障、深冻结、输入竞态、真实 Windows bigint identity、junction/目标竞态、同 slug 并发、窄清理和静态输出。
- 两个冻结 examples 均通过公开 Compiler 在一次性临时模块发布规范固定文件对；无 BOM/尾换行，公开 Runtime Validator 回验 `valid=true`。
- `npm.cmd test`：322/322 通过，既有 R0-R2 harness、R3 contracts/Validator 与模拟语义无回归。
- 浏览器兼容 bundle：Compiler 392,255 bytes；运行源码无 `node:*`、父源码、网络、文件系统、环境变量或 storage，Node 文件能力只在模块根 CLI。
- `npm.cmd run verify`：9/9 步通过；包含 doctor、round scope、boundary、样例、Runtime Pack、Compiler、全部测试、Creator build 与 loopback smoke。
- `npm.cmd run check:boundary`、`npm.cmd run check:round-scope`、`npm.cmd run check:parent-scope -- --base 380c747e62193855c724a947d99a84070ca623ff` 与 `git diff --check`：全部退出 0。

环境事实：第一次最终 `verify` 在已通过 Runtime Pack 与 Compiler 步骤后，第二次全量 Node tests 并发创建临时 Git 仓时遇到 Windows 子进程初始化码 `0xC0000142`；失败来自多项 `git init --quiet`，不是源码断言。只读进程核对确认存活 Node 均为 Codex MCP 或用户既有 OpenClaw，未终止任何进程；在无并行实现代理后原命令单次重跑即 9/9 通过。该瞬态不被伪装为成功测试，也未通过降低并发、跳过用例或修改冻结 harness 规避。

确定性与完整性事实：对象入口先 descriptor-capture 一次规范快照，随后只验证和编译该快照；JSON 入口规范化键序、空白、转义和数字拼写。Compiler 显式映射全部字段与 typed index，保留声明顺序，生成 Authoring SHA、Artifact SHA/UTF-8 byteLength 与独立 Receipt，再调用公开 Runtime Validator 自校验。非法内容原样返回冻结 R1 report；不可恢复故障只暴露固定 `PACK_COMPILER_INTERNAL_ERROR`。

文件边界事实：编译 CLI 只接受模块内相对 `.json`、1 MiB 与安全小写 slug；Runtime/Receipt 回验上限为 16 MiB/16 KiB。发布以同父暂存、`wx+` FileHandle、bigint dev/ino、句柄回读、公开 Validator 与单次目录 rename 完成；已存在、外部 junction、可观察替换和竞态失败关闭，不覆盖或递归删除身份不可信目标。Node 无可移植 `openat`，所以恶意同用户在身份门与 open 的瞬间仍可能留下外部零字节文件，且成功返回后可再次篡改；R3.3 不虚称恶意宿主安全事务。

范围与回退：R1/R2 冻结包、examples、CLI/语义测试及 R0-R2 ADR/验收记录相对固定基线零差异；Creator 与父 `client`、`server`、`.github`、Docker、根 manifest/lock 和 Matrix Oasis 页面零差异。未启动父服务、Docker 或共享栈。逆序 revert 本批提交即可删除 Compiler workspace、CLI、根接线和本批文档，恢复到完整 R3.2a；无数据库、路由、服务、环境变量或运行数据迁移。

## R3.4 验收证据

本批变更严格限于 30 个模块内路径：

- `packages/runtime-pack-simulator/**` 共 10 个文件：private workspace、公开类型、独立 prepared/snapshot/evaluator、安全诊断、说明与测试。
- `packages/game-pack-parity-harness/**` 共 7 个文件：private workspace、包根黑盒锁步适配、可观察投影、说明与测试。
- 语义门：`tests/runtime-pack-simulator-semantics.test.mjs` 与 `tests/game-pack-parity.test.mjs`。
- 模块根接线：`package.json`、`package-lock.json` 与 `scripts/run-verify.mjs`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`docs/BOUNDARIES.md`、`docs/DEPENDENCIES_AND_LICENSES.md`、`docs/KNOWN_LIMITATIONS.md`、`docs/RUNTIME_GAME_PACK.md`、`docs/RUNTIME_PACK_THREAT_MODEL.md` 与本文。

已执行并通过：

- `npm.cmd ci --offline --no-audit --no-fund`：从本机缓存安装 84 packages，退出 0；lockfile 只新增两个内部 workspace link/元数据，无 registry 依赖变化。
- `npm.cmd run test:runtime-simulator`：16/16 通过；覆盖强制 Receipt、source/artifact 双哈希身份、九种 condition、短路与边界、三种 effect、两种 target、顺序工作副本、正负溢出原子回滚、Cue、循环、step limit、快照门与 20 次确定性。
- `npm.cmd run test:parity`：14/14 通过；覆盖冻结 R2 包根黑盒调用、精确中性轨迹、匹配错误诊断、单侧快照篡改、复合快照 round-trip、20 次 Artifact/会话稳定及两个夹具的有界可达状态探索。
- `npm.cmd test`：352/352 通过，既有 R0-R3.3 harness、合同、Validator、Compiler、CLI、Creator 与冻结 R2 语义无回归。
- 严格声明类型检查通过；浏览器 bundle：Runtime Simulator 364,871 bytes、parity harness 446,070 bytes，运行源码无 `node:*`、网络、文件系统、环境变量、storage、父源码或题材专用分支。
- `npm.cmd run verify`：11/11 步通过；包含 doctor、round scope、boundary、样例、Runtime Pack、Compiler、Runtime Simulator、parity、全部测试、Creator build 与 loopback smoke。
- `npm.cmd run check:boundary`、`npm.cmd run check:round-scope`、`npm.cmd run check:parent-scope -- --base 380c747e62193855c724a947d99a84070ca623ff` 与 `git diff --check`：全部退出 0。

语义事实：Runtime prepare 只接受规范 Runtime Pack JSON 与必需 Receipt JSON，并在公开 Validator 完整性门后建立 opaque handle。Runtime 快照使用索引位置与变量数组，并同时绑定 Authoring source SHA-256 和 Artifact SHA-256。evaluator 独立实现且不导入 Compiler、examples、Creator 或冻结 R2 源码；parity harness 只从 R2 与 Runtime 包根调用公开 API，排除有意不同的索引/哈希身份后比较全部可观察状态、inspection、action availability、transition 与 Cue。

夹具事实：中性夹具五步精确到变量、位置和 Cue；末班地铁无需题材特判到达三个 ending，并在显式循环与精确 step limit 下保持 parity。有界 BFS 以位置、变量和步数为状态键，探索所有当前可用 action；样例仍只承担可替换验证职责，不进入运行源码或公共合同。

范围与回退：冻结 R1/R2 文件、Creator、examples 与 R0-R2 历史记录相对 `a888cf5` 零差异；父仓与共享栈零改动。逆序 revert 本批提交即可删除两个新 workspace、语义测试、根接线和本批文档，恢复到完整 R3.3；无数据库、路由、服务、环境变量、Artifact 入库或运行数据迁移。

## R3.5 验收证据

本批变更严格限于 19 个模块内白名单路径：Creator 的 `index.html`、package、App、loader、事务与 CSS；模块根 package/lock；Creator smoke；四个 Creator 测试文件；README、ARCHITECTURE、BOUNDARIES、DEPENDENCIES、KNOWN_LIMITATIONS 与本文。未修改任何 R1/R2 冻结实现、examples、R3 Compiler/Runtime/parity 包或父仓文件。

已执行并通过：

- `npm.cmd install --package-lock-only --offline --ignore-scripts --no-audit --no-fund` 与 `npm.cmd ci --offline --no-audit --no-fund`：84 packages；lockfile 只把 Creator `0.3.0-r3` 的直接内部依赖从 R2 Simulator 切换为 parity harness，无 registry 变化。
- `npm.cmd run test:creator`：23/23 通过；覆盖双侧 prepared/artifact、fatal UTF-8 与 1 MiB、异步 stale、引用 CAS、重置/单步原子候选、parity mismatch、固定 operational 诊断、显式下载与 R0/R2/R3 标识。
- `npm.cmd test`：356/356 通过；R0-R3.4 harness、合同、Validator、Compiler、CLI 与两套模拟语义无回归。
- `npm.cmd run verify`：11/11 步通过；Creator 严格 TypeScript 与 Vite build 转换 247 modules，生产 bundle 446.26 kB；loopback smoke 返回 HTTP 200 并命中 R0、R2、R3 三个稳定标识。
- `npm.cmd prefix`、`npm.cmd ls --all`、boundary、round scope、固定 parent scope 与 `git diff --check` 全部退出 0；boundary checked 139/tracked 137，round/parent checked 81。

原子性事实：Creator 只从 parity harness 公共入口准备 Pack；bundle 同时持有 opaque prepared、规范 Artifact、双侧 snapshot、公共 inspection、Cue 与 transition。异步本地候选、重置和 action 都以捕获的 base session 计算，并在提交时再次做引用 CAS；迟到候选、验证/编译失败、运行失败或 parity mismatch 不改变当前会话。Pack/Receipt 只在明确按钮点击时从当前内存文本下载，不自动保存、不接网络/storage/File System API。

浏览器事实：独立生产 preview 在桌面与 320px 视口真实渲染；320px 下 `clientWidth=scrollWidth=320`，操作与下载按钮高度均至少 44px。末班地铁夹具切换后单步“询问背包学生”到达 `Step 1 / 256`，状态反馈正确，控制台零 warning/error。页面使用通用 Pack 数据，无题材条件分支、动画、渐变、玻璃效果或产品级样例包装。下载动作本批只做静态/单元验证，完整文件下载与更多轨迹留到 R3.6 最终人工验收。

环境事实：未提升权限的 lockfile 更新、`npm ci` 与 build 首次受 `C:\tmp` 沙箱 EPERM 阻止；仅对独立模块的离线 lock/install/build/verify 使用提升写权限后通过。未终止用户进程，未启动父前后端、Docker 或共享栈。浏览器 QA 使用独立 loopback preview，结束后只终止本次创建的两个预览进程。

回退：revert 本批提交即可恢复 R2 Creator 直接依赖与 UI，同时保留完整 R3.4 Compiler/Runtime/parity 能力；无数据库、路由、服务、环境变量、已发布 Artifact 或运行数据需要恢复。

## R3.6 验收证据

工具与环境：Node `24.18.0`、npm `11.16.0`、Git `2.51.0`；Godot 4.6.x 未安装，普通 doctor 如实返回 future optional warning，R3 不创建或下载 Godot 工程。模块仍为 private/UNLICENSED，既有 `esbuild@0.27.7` low 开发期告警与 caniuse-lite CC-BY-4.0 精确批准例外不变。

R3.5 HEAD `ee5d9544a749eddefd800138a9ecb89dd8421358` 的提交前拆分预演已通过：

- `npm.cmd run verify:extraction`：退出 0；subtree 历史保留拆分后模块位于仓库根，standalone 139 个文件，从空依赖完成完整 verify。
- 预演 split commit `8c4b3e11da3851c2de028170abc3461479294b51`、tree `36f97abd9a535451ab16a694d1691baf6d5ace57`、archive SHA-256 `083c9861cb7cce9156688fe1b65617b23a72ad26be7d44423a9551ed2de378fb`。
- 一次性 clone、archive 和日志在成功后由脚本按精确 realpath 清理；`temporaryArtifactsRemoved=true`。这些是 R3.5 预演标识，不替代 R3.6 最终提交后的仓外交付标识。

父仓无回归：在 R3 隔离 worktree 的 `client` 执行 `npm.cmd ci --no-audit --no-fund` 安装 384 packages，随后 `npm.cmd run build` 转换 3055 modules 并退出 0；只有既有大 chunk 与 esbuild allow-scripts 提示。构建后 `git status --short -- client` 为空，固定基线到 R3 HEAD 的 `client` diff 为零。未运行后端、Docker、共享栈、部署或父 Matrix Oasis 路由，因为这些路径零修改且用户要求共享栈重建必须另行确认窗口和基线。

浏览器验收子集在独立生产 preview 完成：

- 页面真实渲染并显示 R0、R2、R3 三个稳定标识；默认中性 Pack 的 Artifact 字节数、完整 SHA-256、操作、变量、Cue 与 Step 可见。
- 切换末班地铁后执行“询问背包学生”，到达“学生的方向”和 `Step 1 / 256`；反馈来自通用 Pack inspection，无题材分支。
- 320px 下 `clientWidth=scrollWidth=320`，操作与两个下载按钮均至少 44px；桌面布局、可见焦点、aria-live 与禁用 action 状态存在。
- 控制台无 warning/error；应用源码边界扫描拒绝父 API、网络、环境变量和 storage。

最终用户人工清单保留以下动作，不以自动测试冒充：

1. 在模块根运行 `npm.cmd run dev`，只使用其 `127.0.0.1` 地址。
2. 中性夹具执行五步至 `ending-pass`；末班地铁分别重置后验证 return、stay、loop 三个 ending。
3. 加载一个合法本地 Pack 和一个非法 JSON，确认非法候选显示“未切换”且旧会话仍可操作。
4. 明确点击下载 Runtime Pack 与 Receipt，确认两文件无自动下载之外的副作用，并可用 `validate:runtime-pack` 回验。
5. 用键盘完成输入切换、action、重置和 details 展开；在桌面/移动宽度复核焦点、无横向溢出、控制台零错误和无父 API 请求。

主线漂移：最终证据收口前 `git fetch origin main` 得 `origin/main=275da0ba5c8f74a993d65022316ae247dedd229b`；相对固定基线，主线 ahead 6、R3 分支 ahead 6，merge-base 仍精确为固定基线，双方路径交集为 0。按用户硬门不 rebase、不解决冲突；若人工批准后选择同步主线，批准失效，必须全量重验并重新人工确认。

本提交后必须在最终 HEAD 再执行 `npm.cmd ci`、`npm.cmd run verify`、`npm.cmd run verify:extraction`、round/parent/boundary、`git diff --check` 与 clean status。最终 HEAD、split tree 和 archive SHA-256 只写仓外交付清单，避免本文自引用；任何失败都会使本页“已收口”结论失效。

R3.6 只提交本验收记录，不改变代码、lockfile、依赖、Artifact 或父项目。revert 本提交只移除验收包；完整功能可继续按 R3.5→R3.1 逆序 revert。用户明确回复“R3验收通过，可以创建PR”前不 push、不创建 PR。

用户明确回复“R3 验收通过，可以创建PR”前不 push、不创建 PR。
