# RPG04 提交前收尾

最终：加载器hash经用户授权同步后，verify:rpg04返回RPG04_AUTOMATED_GATES_OK；旧216＋当前125＝341通过、0失败、0跳过。15项研究、236项模块hash及11项依赖许可hash匹配。以下失败段仅为保留的历史过程。当前状态local_closeout_verified_pr_authorized；仍无整轮人工验收或新协议实测声明，合并须用户审核。

日期：2026-09-09。固定分支 codex/ai-rpg-rpg04-context，HEAD/基线 1b280ed257a45672c4a3dc03745fcb585685faf9。仅两个允许目录；用户最后授权最小收尾后提交PR，尚不授权合并、部署或发布。实际动作见当前机器状态和Git/PR回执。

## 用户确认后的加载器绑定补记

用户人工确认协议正文由审计Agent修改，并明确授权更新加载器hash解除阻断。已仅更新tooling/context-protocol.mjs的固定SHA为当前1a2a24fe…，未修改运行提示词正文或做新实测。旧1250d858…审批和真实证据全部保留；真实六回合属于原协议，不冒充对新审计协议的实测。完整聚合重新运行，实际结果见当前状态。合并必须由用户人工审核。

## 历史收尾阻断（已获授权修复绑定，保留失败）

最终聚合未通过：当前运行协议SHA256为1a2a24fe757cd240802dcf4a1a4307e8846cc31022bca21be8487347b0998382，加载器与原审批固定1250d858cca52778309164981fc333f960b8607c56bc41a683b47085a5fe6734。排除了LF/CRLF差异，既有真实prepared system也不以当前协议文本开头。运行加载器返回RPG04_PROTOCOL_HASH_MISMATCH，上层为RPG04_HOST_PROTOCOL_REJECTED。未修改任何协议、提示词或固定hash；不重新审词、不重新实测。当时机器状态为closeout_blocked_runtime_protocol_binding；后续用户授权修复见上节，不改写本次失败证据。

聚合第一次失败RPG04_LOCK_ROOT是本次新增检查器按JSON键顺序比较依赖导致，已修正为依赖集合比较并新增反例；锁文件不变。第二次通过边界与依赖检查后在host绑定处阻断，因此旧216与完整context测试尚未由本次聚合执行。原审计测试用户确认仍按历史完成保留，不能替代当前失败结果。

## 当前结论与证据优先级

用户本次明确说明三个独立 Agent 工作区导致记录滞后，并要求按审计测试已完成、仅收尾执行。因此不重开提示词、装配或前期审计，也不把本次离线回归冒充新的独立审计。独立审计完成依据为本次用户确认。

当前机器入口为 RPG04_STATUS.json。原状态完整按字节留存 RPG04_STATUS_BEFORE_CLOSEOUT.json；其未实现/待审字段是历史检查点，不得作为当前状态。原账本、审批文件、失败回执及全部历史hash不改。用户质量审批存在交接差异：本地 USER_REVIEW_RESULTS 记录“批准，执行到交接点告知我”，初始交接仍称待审批；保留该原始记录，整轮manualAcceptance=false，最新用户明确授权最小收尾后提交PR；这不改变质量记录，不代表整轮人工验收或合并授权。

## 本轮交付核对

| 批次 | 交付与消费证据 | 收尾结论 |
|---|---|---|
| 04A1/04A2 | 固定边界、0.4.0包、四种上下文辅助合同、/context导出 | 既有交付；旧0.1.0公共合同及业务冻结 |
| 04B1/04B2 | 分离运行协议、批准host、卡包与来源、固定加载器 | 按用户确认已审计；本次仅核验加载/绑定及登记，不重新审词 |
| 04C1/04C2 | selection/compiler、确定性匹配、可见性、预算和已提交历史 | 现有实现；当前聚合阻断，未重跑完整测试集 |
| 04D1/04D2 | 受信适配器、prepared runtime、显式候选提交 | 默认512及旧513拒绝保留；CLI2048和实测4096有意分栏 |
| 04E1/04E2 | JSONL CLI、私有导出、mock取消/恢复与反例 | 原实现及独立测试已完成；本次补package入口，复用历史HTTP回执 |
| 04F | 认证1、失败3、成功叙事6；两世界各3回合 | 10/10，剩余0；没有新派发；所有状态提案未选择 |
| 04G1/04G2 | 新聚合、当前状态、必要文档和MANIFEST | 已补入口与登记；当前执行完整性阻断，见机器状态 |

首个Gu回合为pre-P30正文0.1.0，其余五个为P30正文0.1.1；不是同一提示版本的六回合全覆盖。composed身份仍0.1.0，内容由完整SHA绑定，不替历史成功重新打版本。P30成功不能证明确定性可靠性。首次Gu失败没有raw响应，只保留hash，根因仍未知；其余失败不删除。

普通CLI支持create/register/prepare/generate/commit/read/export，不暴露cancel/discard命令；程序接口已有resumeSession/cancelGeneration/discardTurn，RPG05从该接口消费。CLI不是玩家UI，也不把私有4096一次性驱动当作普通CLI证明。统一预算或扩展CLI能力不在本次收尾中实施。

## 验证范围

- offline：新增聚合反例测试与冻结边界；尝试运行 npm.cmd run verify:rpg04 -- --base 1b280ed257a45672c4a3dc03745fcb585685faf9。聚合定义旧216项原样运行并单列当前context/RPG04测试，但本次在进入测试集前阻断；不运行跨轮旧基线聚合，不改其常量。
- mock：既有 .rpg04-work/e2-http-j2p196_y/harness-receipt.json 为 RPG04_OFFLINE_HTTP_OK，8次fake请求，覆盖两世界提交、query、恢复、取消、非法输出；进程已停止。本次不重启服务、不重新运行HTTP harness，聚合明确标为历史单独证据，不以跳过测试冒充通过。
- real：仅核对既有六回合本地文件hash、账本/receipt/候选/提交绑定和精确模型gpt-5.6-luna；不重新认证、派发或声称服务当前资格仍有效。
- manual/independent：既有输出批准原记录保留；独立审计测试依本次用户确认已完成。本次不是用户质量判断，整轮最终审批待用户。
- license/hash：五个直接依赖、十一个完整包，锁定版本、integrity、resolved和许可证文件SHA；研究清单更新，MANIFEST不含自身hash。许可登记不自动授权第三方内容再分发。
- cleanup：按本轮标签查询无运行容器，18305/18306无监听；本次未创建服务，无需停止。未触碰共享服务、私有会话、凭据。

## 变更、回退与后续

新增 verify-rpg04.mjs 和反例测试，补 test:context、test:context-runtime、rpg:context、verify:rpg04；仅精确登记新验证脚本的子进程权限。除用户追加授权的加载器固定hash外，不改运行源码、提示词正文、资源或输出预算。文档批次更新机器状态、保留原状态、添加本报告，更新入口/路线/审计/MANIFEST。

回退仅撤回本次收尾文件及对应元数据增量，保留进入本任务前的未提交实现、失败证据、所有历史hash和私有输出；不执行reset/clean、删除worktree或清理存储。无公共合同变化、无迁移、无新增依赖。

用户最新明确授权最小收尾后Commit/Push/PR。不自行Merge/Deploy/Release/Publish。RPG05计划已撤下，等待合并后由用户开启计划模式，不混入本轮收尾。

最终定向验收：node --test tests/rpg04-verification.test.mjs 为3通过/0失败；当前研究15项、模块236项hash全部匹配；git diff --check通过。该定向检查时完整聚合仍失败；后续用户授权修复后已重跑同一完整聚合，最终结果见本文开头。

打包检查保留历史文档CRLF原始字节，不批量规范化或重写hash；使用git -c core.whitespace=cr-at-eol diff --cached --check识别换行，其他空白检查仍启用。仅移除新边界脚本文件末尾多余空行，无代码语义变化。
