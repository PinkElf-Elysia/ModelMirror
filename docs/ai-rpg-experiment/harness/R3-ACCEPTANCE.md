# 第三轮：公共运行核心与卡片适配边界 — 离线验收记录

> 历史离线阶段记录：此后用户已通过离线验收，并另行授权有限桥接和真实补测。当前结果与两处父项目授权变更见 [R3-REAL-VALIDATION](R3-REAL-VALIDATION.md)。下文“无真实调用／父项目不改”等描述仅适用于原离线批次。

日期：2026-11-14。**独立原型已实现，离线验证通过，等待用户页面验收。**
没有真实模型调用、共享配置变更、正式入口、部署、数据迁移、Commit、Push 或 PR。

## 1. 本轮结果

第三轮交付的是可复用的回合链路，不是继续扩展修仙玩法：

可信 composition 选择适配器 → 确认开局 → 冻结上下文与状态 → 直接生成或一次原生只读查询 →
最终正文与类型化增量 → 规则求值 → 单记录 CAS 提交 → 面板／JSON／历史与记忆投影 →
续轮、恢复、显式改写或检查点分支。

修仙与原创“灯塔值守”共用 CoreHost、CoreStore、原生查询阶段和 integerDelta。
核心未按题材分支，未 import 修仙定义、旧 Host/Store、样例或面板。
灯塔状态只有 keeper/supplies/duty，不是修仙字段的改名副本。

## 2. 工作树、基线和范围

- 实施树：`C:/tmp/modelmirror-rpg-agent-core-r3`
- 分支：`codex/rpg-agent-core-r3`
- HEAD／冻结基线：`ae67eb2510066a7301825a58de96872c64593298`
- 创建时观察的 origin/main：`22b58ff6b22af8448a7756515fe827bff8e5ae88`。
  local-state、SessionRecords、plugins 与 harness 文档相关比较无新增差异，未浮动替换基线。
- 计划文档从独立计划树按五文件白名单复制并核对哈希；未修改计划树或角色工作树。
- 修改范围：新增 `experiments/ai-rpg-engine/agent-core/**` 及 harness 文档。
  父项目 server/client、旧 local-state、原稿、package/lock、共享栈均未改。
- `agent-core/baseline.json` 冻结 52 个旧文件的 Git 字节与工作区字节摘要，CRLF 差异分别记录。
- 原稿 SHA-256 仍为 `9a1e707828659b3ee1daa9628c334c1782b6526b066287836e7f8db7806a5dc2`。

## 3. 实现文件与端口

路径相对 `experiments/ai-rpg-engine/agent-core/`。

| 文件 | 实际职责 |
| --- | --- |
| contracts.mjs、arithmetic.mjs | 有限 JSON／绑定／适配端口检查；共用整数计算和显式边界策略 |
| store.mjs | 新 scope 和记录格式；正文、状态、事件、回执原子提交；幂等、版本、改写、分支 |
| host.mjs、query-protocol.mjs | 直接／查询续写的唯一执行链；最多两次生成；未知 lookup；签名与 tool ID 续传 |
| simulator.mjs | 持久模拟回执、确定性样例与故障注入，不包含真实网络派发 |
| composition.mjs、manifest.mjs | 静态登记、可信注入及运行依赖内容绑定 |
| adapters/cultivation/index.mjs、rules.mjs | 修仙内容／查询／表现复用，语义规则迁接，共用整数助手 |
| adapters/lighthouse/index.mjs | 独立 schema、查询域、两种操作及原创成人样例 |
| server.mjs、public/* | 默认关闭、仅 loopback 的独立聊天页面与 HTTP 入口 |
| measure.mjs、*.test.mjs | 离线请求组成、差分、恢复、结构及 HTTP 证据 |
| README.md、baseline.json | 启动／接入说明与冻结依据 |

SessionRecords 直接复用，未新建数据库或复制底层存储。
修仙规则的语义分支来自冻结 v2；数量、账户和关系数值分支使用新公共助手，旧函数仅作差分参照。
关系变化以 before/after 保留实际生效结果；灯塔变化另含 requestedDelta/actualDelta。

每次加载记录核对启动时实际绑定。manifest 跟随静态模块依赖并纳入显式资源和 package-lock。
它不是第三方执行沙箱，也不是整个 node_modules 内容的完整性审计。
原生供应商 metadata 保存在记录中，页面普通投影不暴露；可由离线证据导出核对。

## 4. 两个独立验收页面

预览在 `127.0.0.1:18841`，只读入当前工作树的测试数据：

- [新建／选择验证配置](http://127.0.0.1:18841/)
- [修仙：查询后养息丹 5→3](http://127.0.0.1:18841/?adapter=cultivation&session=core-4a74c52835f5a96ebced9c2ff718efa3ee6b7711)
- [灯塔：查询后灯油 5→3](http://127.0.0.1:18841/?adapter=lighthouse&session=core-bc029879a74577e04d1dc5faa3b2a87ad3b2f75d)

已在内置浏览器实际操作两种开局、查询续写，确认完整面板与正文；灯塔刷新保持 3 份、绷带保持“未设定”。
开发检查中实际操作最终回执未知→核对→只提交一次，UI 没有提供跳过未知的结束按钮。
最终灯塔页面另已实际预览并采用一次空变更候选，刷新后共两个完成回合，灯油仍为 3。
最终灯塔页面无横向溢出，最终两页未观察到控制台错误。未做移动设备全套验收。
页面固定样例不是通用语言模型：非样例自由输入可能只返回空变更。

可在“离线故障与候选设置”选择故障，在“回合阶段与查询依据”查看请求链；
“改写、检查点和停用”提供原因、前后差异、分支和停止新回合。
“保存离线证据”下载原始模拟请求／工具调用／回执、最终状态及记忆投影。

当前预览进程为本任务创建，PID 启动记录在本次交接中为 44604（停止前须重新核对命令行，不能只依赖旧 PID）。
启动命令：

```powershell
node experiments/ai-rpg-engine/agent-core/server.mjs --offline-preview --port 18841
```

数据与日志：`experiments/ai-rpg-engine/.rpg03-work/agent-core-r3/`，已由现有忽略规则排除。
开发中更改依赖指纹前产生的临时会话保留，不自动转换；以上链接是最终代码下新建的会话。
最终原始回复与工具数据在本地记录中，未加入跟踪文件。

## 5. 验证命令与结果

全部为离线／模拟；没有供应商请求。前端为新增原生 JS 页面，未改 TypeScript 或父后端，因此没有扩大为父项目构建／全量测试。

| 检查 | 本次结果 |
| --- | --- |
| 新 agent-core 全部测试 | **30/30 通过**；包含多场景循环 |
| 旧 local-state 根及 tools 下测试 | **57/57 通过**，26.7 秒 |
| 变量面板、变量运行与商店存储 | **38/38 通过**，114.0 秒 |
| 浏览器脚本／服务器 node --check | 通过 |
| 冻结旧文件摘要与核心依赖边界 | 新测试内通过 |
| 浏览器实际操作 | 两适配开局、查询、面板、刷新；未知恢复已观察 |
| 真实供应商／用户最终验收 | 未执行／待用户确认 |

合计 **125 项测试通过**。本轮未重跑历史 CI 门禁；实施前审计中记载的 CI 状态不冒充本轮结果。

```powershell
$newTests = Get-ChildItem experiments/ai-rpg-engine/agent-core -Recurse -Filter '*.test.mjs' | ForEach-Object FullName
node --test --test-concurrency=4 @newTests
$legacyTests = @(Get-ChildItem experiments/ai-rpg-engine/local-state -Filter '*.test.mjs'; Get-ChildItem experiments/ai-rpg-engine/local-state/tools -Filter '*.test.mjs') | ForEach-Object FullName
node --test --test-concurrency=4 @legacyTests
node --test experiments/ai-rpg-engine/authoring/variable-panel.test.mjs experiments/ai-rpg-engine/authoring/variable-runtime.test.mjs experiments/ai-rpg-engine/harness/shop-store.test.mjs
node --check experiments/ai-rpg-engine/agent-core/public/app.mjs
node --check experiments/ai-rpg-engine/agent-core/server.mjs
node experiments/ai-rpg-engine/agent-core/measure.mjs
```

新测试原始日志在非跟踪 `.rpg03-work/agent-core-r3/verification/core-tests.txt`。
最初 HTTP 测试的 Host 头被 fetch 规范化，后改用 node:http 真正发送非法 Host；
另修正测试 after 清理顺序，先关闭所有权记录再清理临时目录。未放松服务器 Origin 校验或存储保护。
收尾审查还修复了候选采用的中断窗口：已选择的候选回复与回合初始化同次提交，进程中断后恢复不生成替代候选。新增故障注入回归通过。

| 计划验收 | 对应证据 |
| --- | --- |
| V01–V03 初值、数值、语义 | contracts/arithmetic/adapters/host/store 测试，严格确认、null、5→3→0、档案保留、原规则差分 |
| V04–V05 直接／查询 | host/protocol 使用同一链，普通一次、查询两次、工具 ID／metadata、域／覆盖范围 |
| V06–V07 幂等与拒绝 | host/store：重复、异内容、过期、CAS 竞争、无效增量、参数截断、明确失败及未知 |
| V08 中断／恢复／投影 | beforeCommit/afterCommit 故障、真实子进程中断重启、只 lookup 原回执、投影失败不生成 |
| V09–V10 改写／分支／停用 | 候选不写入、唯一采用、原因和前后状态、检查点隔离、绑定漂移保留记录 |
| V11 插件装配 | 实际修仙 Mod、前后置词、世界书、正则、总结／记忆输入；工具不冒充用户输入 |
| V12 公共复用 | 核心无卡片依赖检查、不同根 schema、共同整数助手、UI 与 JSON 同源 |
| V13 旧模式 | 52 文件冻结、57 项旧状态回归与 38 项相关回归 |

V11 证明的是实际文本装配和投影，不是正式插件目录安装流程或付费总结／记忆执行。
本轮只有当前世界叙事者；只读查询范围不等于角色私有认知隔离。

## 6. 离线测量

汇总验证清单：[R3-VERIFICATION.json](R3-VERIFICATION.json)。

完整机器可读结果：[R3-OFFLINE-MEASUREMENTS.json](R3-OFFLINE-MEASUREMENTS.json)。

| 配置 | 直接生成请求字节 | 查询＋续写累计请求字节 |
| --- | ---: | ---: |
| 修仙 | 537,799 | 1,076,569 |
| 灯塔 | 1,200 | 3,247 |

这是各自冻结开局下的实际模拟请求序列，不是两个题材之间的成本优劣比较。
修仙固定／状态装配仍合并在系统消息中（528,857 字节）；查询回合会再次携带它。
token、供应商费用、可靠正文首字均为未测，不用字符数或模拟耗时推算。
真实调用数为 0；模拟 usage 中的 $0 不代表真实供应商报价。

## 7. 限制、下一轮与回退

- 新核心尚未通过真实 RPG 后端组合验证；固定工具的 Python 路由原样保留。灯塔不派发到真实路由。
- 保留第一／二轮残余叙事问题，本轮未补全文事实审查或未登记物品补丁。
- 状态记录有容量限制，原型不适合无限长历史；不新增通用归档或迁移体系。
- 本轮模拟 options/preview 是明确的离线端口；不是生产供应商适配承诺。未来模型策略接入须验证预算、路由与恢复。
- 同名角色去重／唯一数量变化属于修仙策略，不上升为通用角色身份或语义权限系统。
- 尚无 S2 角色侧接入、多 Agent、工具注册平台、联网搜索、地图、检定、小说编辑或世界包编辑器。
- 下一轮先做上下文与生成职责分配；本轮的接口、请求构成和事件—正文关联供它复用。用户未授权自动进入下一轮。

回退只关闭本轮自有预览并撤销新入口，旧模式不受影响。
保留新格式存档、原回执和所需源码版本；遇到绑定漂移不删数据、不猜测迁移、不重放模型请求。

### Preview resume check

The task-owned preview was restarted on the same port and data directory after the previous process exited. Both saved acceptance sessions loaded with the same manifest and state revisions (cultivation 1, lighthouse 2); the root page returned HTTP 200. No generation was dispatched. Current process: 44604; resume logs are in the ignored task data directory.
