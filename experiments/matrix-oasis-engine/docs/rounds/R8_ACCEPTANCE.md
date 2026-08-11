# R8验收记录

状态：R8.6 验收证据已收口，等待本地提交及提交后最终拆分复验。

固定基线：`21cbbb8b943b6f9d9799f014c44a6349e6124a63`

## 批次

- [x] R8.1 治理与关键路径护栏
- [x] R8.2 Generation Proposal与Scene Blueprint合同
- [x] R8.3 OpenAI兼容模型适配器
- [x] R8.4 生成编排、修复循环与CLI
- [x] R8.5 真实模型资格验证
- [x] R8.6 standalone与验收收口

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

R8.3提交：`00a74f6a5d13f56f2a32290ee5e71649ddf97d8e`。单独revert该提交删除Provider workspace与Provider测试，不影响R8.2离线合同或冻结运行链。

## R8.4证据

- `@matrix-oasis/prototype-generator`公开面固定为供应商中立的`generatePrototype`、OpenAI兼容Provider工厂和静态operational error；生成器只从冻结包根调用合同、Compiler与Runtime，不导入内部evaluator或样例。
- 最多一次初始请求和两次定向修复；修复负载不重发prompt，只含上一候选和静态code/JSON Pointer，三次内容失败后只返回diagnostics且不写文件。
- 成功链严格通过Generation Proposal、冻结Authoring Validator、Compiler、Receipt canonical化、Runtime prepare与初始Session，并要求至少一个声明Action；输出固定为五个canonical JSON，generation report只含模型、请求数、usage、artifact hash/字节数和Runtime检查。
- `plan:prototype-call`只检查上传范围，不发请求；`generate:prototype`必须显式确认上传，只读取三个R8专用环境变量。prompt限制为`C:\tmp`内32 KiB fatal UTF-8普通文件，输出限制为`C:\tmp`尚不存在的一级子目录。
- 五文件通过同父临时目录、独占FileHandle、bigint设备/文件身份、同步/回读和单次目录rename事务发布；覆盖junction、读取换身、现有目标、第二文件故障、并发同名和路径越界，失败不覆盖既有目标或发布半成品。
- `npm.cmd install --package-lock-only --offline --ignore-scripts --no-audit --no-fund`退出0；lock只为prototype-generator登记三个既有内部workspace依赖，无新registry包或许可证例外。`npm.cmd ls --all`无missing/extraneous。
- `npm.cmd run verify:prototype-generation`：合同14/14、Provider/CLI 28/28、生成编排9/9通过；20次假Provider产物字节一致，真实loopback CLI完成一次严格Schema请求并发布五文件。
- TypeScript declaration strict解析；`npm.cmd run check:boundary`通过（checked=895、tracked=890），包含新增“变量凭据引用允许、真实明文密钥仍拒绝”正反锁；round/parent scope与固定BASE通过（checked=57、changed=41），`git diff --check`通过。
- 注入仓外Godot 4.6.3后的最终`npm.cmd run verify`：14/14步骤通过；Node 536/536、冻结Godot R4–R7、Creator build与HTTP smoke无回归。
- 一次未注入`GODOT_BIN`的全量Node试跑仅有doctor严格环境项失败（535/536），设置方案固定的仓外Godot路径后最终门通过；未修改doctor或放宽工具链。
- R1–R7、Creator、Godot、examples、assets、vendor及历史验收记录相对R8.3 HEAD零差异；未调用真实模型、Marble或Meshy，未启动Docker、父服务或共享栈。

R8.4提交：`04a56020f11cf542c742f30f15490a2388fc3e96`。单独revert该提交删除生成编排、两个CLI和事务测试，恢复R8.3仅Provider状态，不影响冻结R1–R7链。

## R8.5证据

- 在用户当次批准的上限内，对OpenAI官方Chat Completions endpoint与`gpt-5.6-luna`执行一次固定中性提示资格链；最多3次、费用上限1美元，本次恰使用3次请求，报告usage为prompt 18,207、completion 3,869、合计22,076 tokens。授权额度已耗尽，后续不再发起外部请求。
- 资格脚本固定使用仓内中性提示，只读取三个R8专用环境变量；真实API key仅存在于仓外操作者脚本和当前进程环境，未读取父仓网关变量，也未进入仓库、产物、日志或测试输出。
- 为兼容严格Structured Outputs，Provider侧Schema投影机器化固定为：移除模型侧`$id`、`not`、`uniqueItems`，将`oneOf`映射为`anyOf`，把对象全部properties列为required，并将嵌套`$defs`提升到唯一顶层表后重写引用。完整本地Generation Proposal Schema、跨合同语义、Compiler和Runtime仍是最终准入门，未因供应商兼容放宽产物合同。
- 真实资格链无需人工修改JSON即发布固定五个canonical产物；目录无额外文件、BOM或尾换行，固定资格提示全文、endpoint、凭据标识、Authorization值和原始HTTP响应均未进入产物。
- 离线复核确认四个业务artifact的SHA-256与UTF-8字节数均与generation report一致；Authoring Validator、Generation Proposal Validator与Runtime Validator均valid，Runtime可prepare并创建active初始Session。报告记录2个声明Action、1个初始可用Action和`requestCount=3`。
- `npm.cmd run verify:prototype-generation`：合同14/14、Provider/CLI 29/29、生成编排9/9、资格CLI 5/5，共57/57通过；`node --test tests/boundary.test.mjs`为68/68，`check:boundary`为checked=897/tracked=897，round scope为checked=52/changed=43，`git diff --check`通过。
- qualification脚本不进入普通`verify`，因此自动验证不会产生费用；真实五文件及供应商调用结果仍只保存在仓外。未调用Marble或Meshy，未启动Godot、Docker、父服务或共享栈。

R8.5提交：`8feb7d9ca8053c21f99f861fec9df287b79729ac`。单独revert该提交删除真实资格CLI与其测试，并恢复Provider兼容投影前的R8.4状态；不会删除仓外真实产物，也不影响冻结R1–R7链。

## R8.6证据

- `npm.cmd ci --no-audit --no-fund`：88 packages，退出0；`npm.cmd prefix`精确指向独立模块根，`npm.cmd ls --all`退出0且无missing/extraneous。仅保留既有`esbuild@0.27.7` install-script提示，无新registry依赖或许可证例外。
- 注入仓外Godot 4.6.3后，最终功能树上的`npm.cmd run verify`为14/14步骤通过：doctor、round scope、boundary、冻结Godot R4–R7、examples、Runtime Pack、Compiler、Runtime Simulator、parity、Scene Pack、Prototype Generation、Node全量测试、Creator build和HTTP smoke全部通过；Node总计546/546，Creator为247 modules。
- Godot严格门确认4.6.3；GdUnit4、官方movement reference、Kenney资产、R5 adapter/parity、R6 3D和R7 Scene均复验通过。R8未修改Godot工程、资产、vendor或既有3D执行链。
- `npm.cmd run verify:prototype-generation`为57/57；真实资格命令不属于普通verify，完整回归未发起任何模型或供应商请求。
- `npm.cmd run check:round-scope`与固定BASE的`check:parent-scope`均为checked=43/changed=43；`check:boundary`为checked=897/tracked=897；`git diff --check`与工作树clean检查通过。R1–R7、Creator、Godot、examples、assets、vendor和历史验收记录相对固定BASE均零字节差异，全部43个变更路径只在R8 allowlist内。
- 父`client`在独立worktree执行clean `npm.cmd ci --no-audit --no-fund`和`npm.cmd run build`均退出0：384 packages、3,074 modules；构建后client源码、manifest与lock零差异。仅有既有esbuild脚本提示和大chunk warning。
- 父后端、Docker、共享栈、部署和父路由均未运行；未调用Marble或Meshy，也未启动Godot图形预览。R8退出只证明自然语言到合法原型描述、Runtime产物与初始基础交互，不宣称已生成真实3D资产或完成自然语言到3D闭环。
- 本文档提交后必须在最终HEAD重新执行`verify:extraction`及范围/差异门；最终commit、split tree、archive SHA-256和真实资格artifact hash只写入仓外交付清单，避免文档自引用。若最终拆分失败，本节完成状态立即失效。

本批提交SHA仅在仓外交付清单记录。单独revert本提交只删除R8.6验收证据；逆序revert R8.6至R8.1六个提交即可恢复完整R7模块，不涉及数据库、服务、Godot生成数据或远程供应商任务。
