# R3 验收记录

状态：R3.1-R3.2 已提交；R3.2a UTF-16 规范化兼容修正已完成并验证，等待本地提交；R3.3-R3.6 尚未实施

固定基线：`380c747e62193855c724a947d99a84070ca623ff`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [ ] 全部 R3 变化严格位于模块目录并符合 schema v3 正向 allowlist。
- [ ] R1/R2 冻结路径相对固定基线零差异。
- [ ] Runtime Pack/Receipt 合同、Compiler、独立 Runtime Simulator 与 parity harness 按批次通过。
- [ ] Creator 双执行实验台在合法、拒绝和竞态场景保持原子锁步。
- [ ] 完整 verify、独立拆分、父前端无回归与浏览器人工验收通过。
- [ ] 父源码、Matrix Oasis 占位、配置和共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R3.1 | 治理、精确 allowlist 与 R1/R2 冻结 | `27a33d65e8eb7ed43821a907ae991797449ed5bc` | 已完成 |
| R3.2 | Runtime Pack/Receipt 合同与 Validator | `c019585420ca9e9c3d979a98767699a95e842586` | 已完成 |
| R3.2a | 冻结 R1 字符串域的规范化兼容修正 | 本批提交；SHA 由下一批或仓外交付清单记录 | 已验证，等待本地提交 |
| R3.3 | 确定性 Compiler 与安全 CLI | 待提交 | 未开始 |
| R3.4 | 独立 Runtime Simulator 与 parity harness | 待提交 | 未开始 |
| R3.5 | Creator 双执行锁步实验台 | 待提交 | 未开始 |
| R3.6 | 拆分、无回归、浏览器与证据收口 | 待提交 | 未开始 |

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

用户明确回复“R3 验收通过，可以创建PR”前不 push、不创建 PR。
