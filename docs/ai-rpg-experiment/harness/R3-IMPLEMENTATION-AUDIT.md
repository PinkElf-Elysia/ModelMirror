# 第三轮前实现审计与复用边界

核对日期：2026-11-14（America/Phoenix）。这是只读代码与既有证据核对，未重跑历史测试或调用模型。

## 基线与状态

- 已 fetch 并固定 origin/main：`ae67eb2510066a7301825a58de96872c64593298`，包含 [PR #472](https://github.com/PinkElf-Elysia/ModelMirror/pull/472) 的工具 v2；其实现提交为 `f607d7c4d808e1f3924ecb5ebc48a881a4c714b2`。
- 计划文档工作树：`C:/tmp/modelmirror-rpg-agent-core-r3-plan`，分支 `codex/rpg-agent-core-r3-plan`，创建时干净。不改已验收树和角色工作树。
- 修仙原稿 SHA-256：`9a1e707828659b3ee1daa9628c334c1782b6526b066287836e7f8db7806a5dc2`，与现有 SOURCE_HASH 一致。
- 第二轮历史证据：Node 57 项、Python 75 项通过；3 次真实请求合计 USD 0.393111，最终单次补测复用前次查询，丹药 5→3，其他状态不变，用户接受原型。不是新两请求全链重测，也不证明长期质量。
- 主线合并不等于 CI 全绿：当前查询显示 Frontend quality、Backend quality、Offline authoring contracts 失败；Windows Project Host 成功。前端日志涉及 HarryPotter 测试类型和 shop-client-measurement 声明；后端涉及 Provider coverage 登记断言；authoring 日志 11 项失败，包含缺 httpx 和隔离副本缺 plugins/voice-library.mjs。本次未复跑归因、不标记为全是既有问题、不修 CI。

历史范围与实测明细见 [v2 收尾](../local-state/TOOLS-V2-CLOSEOUT.md) 和 [脱敏统计](../local-state/tools-v2-real-summary.json)。

## 已读取的实际实现

以下路径均相对 `experiments/ai-rpg-engine/`，后端路径除外。复用决定是第三轮工程建议，不表示公共核心已存在。

| 实现与代码锚点 | 当前事实 | 第三轮处理 |
| --- | --- | --- |
| `authoring/session-storage.mjs`：SessionRecords.open/get/put/reconcile | scope、单目录所有权、CAS、pending 标记、提交前后故障注入；可自定义记录校验 | 直接复用，不新建存储后端，不把恢复说成任意断电保障 |
| `local-state/definition.mjs`：validateState/blankState | FORMAT 看似通用，但字段、四档灵石、装备槽、关系范围／标签全部写死修仙 | 留在修仙适配，不将这份结构命名为通用世界结构 |
| `local-state/rules.mjs`：applyOperations/decodeReply/candidate | 类型化增量、整数计算、未知初值、不足拒绝、关系钳位；解析和重复面板规则也绑定修仙 | 抽取被两适配实际使用的数值原语；语义操作、关系区间、正文标记归适配，避免任意 JSON 路径写入 |
| `local-state/store.mjs`：validateRecord/finish/fork | 存储直接 import 修仙 validateState 与 candidate；同记录提交正文／状态／事件，检查点分支 | 复用行为和测试依据，形成接收适配端口的公共记录层；旧格式与读取路径不改 |
| `local-state/host.mjs`：open/view/turn | 直接加载修仙适配、开场、面板、模拟变体及上下文 | 新公共宿主由可信启动代码注入适配器和供应商；模拟选项离开公共业务合同 |
| `local-state/tools/host.mjs`：ToolsHost/advance | 继承修仙宿主；冻结状态、最多两阶段、查询后续写、未知核对、最终提交 | 抽取现有受限执行语义，不增加工具轮数或备用模型；旧 ToolsHost 留作冻结兼容入口 |
| `local-state/tools/query.mjs` 与 `protocol.mjs` | 查询绑定六类 domain 及修仙 kind；协议检查绑定单个 TOOL；续写保留原 assistant、tool ID、元数据 | 通用承载可注入一个受信查询定义／执行器；仍只一个原生只读工具，不提前建设工具注册平台 |
| `local-state/tools/bridge-provider.mjs` | 原生桥接、预算及回执已实现；依赖 definition 中通用帮助函数；selection 为整条链固定模型 | 保存为现有受控边界，本轮不改变线路、凭据、预算策略；生产可组合接入后续再做 |
| `local-state/adapter.mjs`、`context.mjs` | 固定原稿指纹、定点去面板；世界书触发使用本地面板投影后丢弃；最终历史不带全量面板 | 修仙适配继续调用现有装配，保持实际 messages 语义；大上下文优化在第四轮 |
| `local-state/panels.mjs`、`server.mjs` | 面板按修仙字段渲染；服务器 bootstrap/query 直接引用修仙样例和规则，不能只替换 hostFactory 就称通用 | 独立预览入口注入适配，面板与初始化由适配提供，HTML继续受限渲染 |
| `authoring/variable-panel.mjs`、`variable-runtime.test.mjs` | 旧变量合同要求全量 JSON；后者是测试文件，不存在同名运行实现 | 复用模板渲染与相关测试，保留旧合同；不作为增量／数值引擎 |
| `harness/stage-runner.mjs`、`measurement.mjs` | 商店已有阶段主备配置、累计预算、恢复；绑定商店事件／记录；已有公共计量 | 本轮只复用计量，阶段策略留第六轮；不能直接套商店账户或 event 结构 |
| `authoring/comparison-host.mjs`、`comparison-ledger.mjs` | 候选协调、唯一采用、授权预留和恢复；依赖 authoring 宿主与其合同 | 候选预览／采用行为作为参考；不把整套候选对比搬入第三轮，不宣称阶段协作已完成 |
| `runtime/core.mjs`、`runtime/plugin-host.mjs` | 旧 RPG runtime 已有注入 store/modelAdapter/pluginHost 的端口和冻结合同 | 借鉴端口分离；不修改旧 runtime 的 schema 和历史门禁，也不把它直接视为新状态账本。新边界须与其职责区分 |
| `server/model_router/rpg_local_state_tools.py` | 后端严格要求 tools == [TOOL]，不是任意工具网关 | 不放宽后端；第二适配本轮仅模拟，不能声称已取得真实供应商支持 |

## 尚未完成的框架能力

1. 公共记录层仍能看到具体字段，宿主仍依赖修仙上下文／面板。改名无法完成复用。
2. 对话框架与模型策略未统一：商店阶段、原生查询和角色侧是不同运行实现；本轮只解耦当前本地状态链，不统一全仓框架。
3. quantityOnly 是明确宿主约束，不是自动理解“其他不变”的通用规则。strictRelations 的同名防重是修仙策略，不应升级为未来角色实例身份规则。
4. 查询仅指当前会话已登记状态，不是角色私有认知权限。工具声明和持久化 metadata 不等于第三方插件安全沙箱。
5. 系统消息仍约 528 KB，查询两次累计请求约 1.075 MB；真实输入每次约 15.7 万 token。数字来自第二轮证据，未在本次重测；本轮不能承诺降低费用。
6. 叙事补写未登记物品／容器、直接精确余量和可靠正文首字缺口按用户要求保留，第三轮不新增定点补丁。

## 对角色与主项目能力的使用边界

本阶段不依赖角色工作完成，不复制其未提交文件、不重新请求其推进，也不将共享记忆策略写成决定。当前角色工作内容只作为历史线索，不在本次审计中确认最新合并、实测或部署状态。

本次未重新审计主项目整个 Agent／工作流。历史已有受限执行、权限和任务能力不等于可直接替代 RPG 运行；第六轮确需对接时再按具体接口核对，不能借复用提前改 /api/chat 或全项目工作流。

## 第三轮应证明的最小差异

一个异题材测试配置必须具有不同根字段、无修仙关系／账户／装备结构，同时使用同一记录、操作编号、版本校验、查询阶段、恢复和提交出口。原始输入输出可以不同，公共宿主不应出现按 cultivation 或样例名称分支的代码。两边共同使用有明确边界的数值运算原语，不只是挂接两个完全独立引擎。
