# R8验收记录

状态：R8.3 Provider已验证，等待本地提交。

固定基线：`21cbbb8b943b6f9d9799f014c44a6349e6124a63`

## 批次

- [x] R8.1 治理与关键路径护栏
- [x] R8.2 Generation Proposal与Scene Blueprint合同
- [x] R8.3 OpenAI兼容模型适配器
- [ ] R8.4 生成编排、修复循环与CLI
- [ ] R8.5 真实模型资格验证
- [ ] R8.6 standalone与验收收口

每批在验证后提交；后续批次记录前一提交SHA。真实模型输出、最终HEAD、split tree和archive hash只记录在仓外交付清单，避免自引用或提交供应商内容。

## R8.1证据

- 从 `origin/main@21cbbb8b943b6f9d9799f014c44a6349e6124a63` 创建独立分支/worktree；该基线包含已合并R7，原主工作区未修改。
- 本批仅迁移schema v8、精确allowlist、广义冻结根、关键路径文档和唯一Provider网络例外；未修改apps、examples、既有packages、Godot、资产或vendor。
- `npm.cmd ci --offline --no-audit --no-fund`：86 packages，退出0；无新registry依赖。
- `node --test tests/round-scope.test.mjs tests/boundary.test.mjs`：116/116通过；覆盖四类Git状态、父仓、冻结根、精确Provider路径和网络旁路负测。
- `npm.cmd run check:round-scope`：通过，checked/changed均为21；`check:parent-scope`与固定BASE通过。
- `npm.cmd run check:boundary`：通过，checked=877、tracked=871。
- 注入仓外Godot 4.6.3后 `npm.cmd run verify`：13/13步骤通过；Node 484/484、Godot R4–R7、Creator build与HTTP smoke无回归。
- 未启动父服务、Docker或共享栈，未调用真实模型、Marble或Meshy。

R8.1提交：`968be5d75335b27829f32f374c393cc7b945259a`。单独revert该提交恢复完整R7治理和模块版本，不影响冻结运行链。

## R8.2证据

- 新增私有 `@matrix-oasis/prototype-generation-contracts@0.1.0-r8`；Generation Proposal在运行时组合冻结Authoring Schema，不维护第二份Authoring字段定义。
- Scene Blueprint `0.1.0`只包含scene提示、zone、asset brief、placement与node binding；不含路径、哈希、供应商任务、3D坐标、密钥或原始用户提示。
- 严格文本入口按parse、闭合Schema、冻结Authoring Validator、跨合同语义顺序门控；覆盖重复键、256层深度、集合/文本预算、孤立代理项、身份、ID、environment、entity/zone/asset/placement/node引用。
- `npm.cmd ci --offline --no-audit --no-fund`：87 packages，退出0；lock只新增本地workspace登记，复用既有Ajv `8.20.0`与jsonc-parser `3.3.1`。
- `npm.cmd run test:prototype-contracts`：14/14通过；20次canonical Proposal字节稳定，输入不变，成功值与报告深冻结。
- TypeScript declaration strict解析、`npm.cmd prefix`、`npm.cmd ls --all`、`check:round-scope`、固定BASE的`check:parent-scope`、`check:boundary`和`git diff --check`均通过；boundary为checked=884、tracked=877。
- 注入仓外Godot 4.6.3后最终 `npm.cmd run verify`：14/14步骤通过；Node 498/498、Godot R4–R7、Creator 247 modules build与HTTP smoke无回归。
- 首次非提权全量Node测试因沙箱拒绝 `C:\\tmp` 临时目录而出现6项环境失败；按原命令在允许的仓外临时根重跑后498/498。一次完整verify在既有boundary临时Git fixture启动时瞬态失败；该fixture随后63/63，最终完整verify通过，未修改或放宽既有测试。
- R1–R7 packages、examples、Creator、Godot、assets、vendor及历史验收记录相对R8.1 HEAD零差异；父仓选定范围零差异。
- 未调用真实模型、Marble或Meshy，未启动Docker、父服务或共享栈；R8.2完全离线。

R8.2提交：`c3e2feffda8995d2a882e83a29385180b0f84c18`。单独revert该提交删除唯一新增合同workspace及根验证登记，不影响冻结R1–R7链。

## R8.3证据

- 新增私有 `@matrix-oasis/prototype-generator@0.1.0-r8`；本批公开面仅为OpenAI兼容Provider与固定operational error，生成编排留给R8.4。
- Provider只调用精确 `/v1/chat/completions`，固定non-streaming、strict `response_format=json_schema`、120秒超时、1 MiB响应上限、redirect拒绝、无tools/函数调用/自动重试。
- 外部endpoint必须HTTPS，HTTP只允许loopback；Provider源码不读取环境变量、父仓网关配置、storage或文件系统，凭据保存在不可观察内部状态。
- 首轮只发送prompt；修复请求只发送上一候选、静态code/JSON Pointer和原始Schema。只接受单一choice、文本content与`stop`，错误统一为静态`PROTOTYPE_GENERATOR_INTERNAL_ERROR`。
- `npm.cmd ci --offline --no-audit --no-fund`：88 packages，退出0；lock只增加本地prototype-generator workspace，无新registry依赖。
- `npm.cmd run test:prototype-provider`：19/19通过；真实loopback覆盖请求结构、修复负载、HTTP/HTTPS gate、超时、1 MiB前后界、redirect、HTTP错误、畸形编码/JSON/envelope、credential与异常脱敏，确认失败不重试。
- `npm.cmd run verify:prototype-generation`：合同14项加Provider 19项全部通过；TypeScript declaration strict解析、boundary、round/parent scope与diff-check通过。
- 注入仓外Godot 4.6.3后最终 `npm.cmd run verify`：14/14步骤通过；Node 517/517、冻结Godot R4–R7与Creator build/smoke无回归。
- R1–R7、Creator、Godot、examples、assets、vendor及历史验收记录相对R8.2 HEAD零差异；未调用真实模型、Marble或Meshy，未启动Docker、父服务或共享栈。

本批提交SHA由R8.4记录。单独revert本提交删除Provider workspace与Provider测试，不影响R8.2离线合同或冻结运行链。
