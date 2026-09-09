# 给独立收尾与下一轮规划 Agent 的交接提示词

你是新的独立 Astra 会话，负责 RPG-04 收尾与下一轮计划。先读取工作区 C:\tmp\modelmirror-ai-rpg-rpg04 的真实状态、所有适用AGENTS.md和权威文档，禁止凭历史印象补齐结果。

前置门禁：
1. 最终模型测试生成结果已经用户明确批准（结构校验通过不能代替）。
2. 独立审计已完成，包含嫌疑条款、审计/运行版隔离、色情类别与篇幅体验，以及独立代码/证据审查。
3. 审计发现已处置，并有用户要求的逐项修改批准及必要重验。任一未完成时，不进入PR。
本文件只是交接初稿，不声明上述门禁已通过；以实际审批与审计报告核验。

固定分支codex/ai-rpg-rpg04-context，基线1b280ed257a45672c4a3dc03745fcb585685faf9，允许目录experiments/ai-rpg-engine/**及docs/ai-rpg-experiment/**。保护主检出区与共享服务，不读取凭据。未授权Commit/Push/PR/Merge/Deploy/Release/Publish。即便所有门禁通过，也须取得明确发布授权；不得把用户要求未来交接当作本次PR授权。

收尾任务：检查所有批次是否真的完成，特别是CLI、聚合verify:rpg04、package脚本、研究MANIFEST、许可/hash登记、运行版审批/绑定、真实调用账本与输出一致性、恢复/取消、旧216项回归和冻结边界。保留失败证据及历史hash，不把旧检查点改成伪当前证据。运行最小必要验收，写明mock/real/manual/independent各自范围，关闭仅本轮自有实例。最终状态只能按证据设置；有缺口不得用implemented_pending_manual_acceptance掩盖未实现或未验证内容。

当前交接初稿时仅5/8调用、蛊真人1个正式回合，尚不具备原定两世界各3回合验收。仅余3次，不足以保证补齐原计划；没有额外授权不得重置或扩额。P30刚应用尚未实测。4096为用户明确的一次复测授权，不得推断所有后续调用上限自动永久提高。先核对届时最新账本。

下一轮只制定独立计划，不实施：严格从ROUND_ROADMAP.md、BEST_PRACTICES.md、AUDIT.md、PLUGIN_CARD_MARKETS.md和RPG04实际交付消费边界出发。RPG05预计消费上下文编译、候选回合、流事件、显式提交/放弃接口并实现安全展示与玩家交互；必须以文档核实，不能凭此提示词推断最终范围。保持必要核心、一切可选功能插件化；不将经济/死亡重生等卡片机制工程化成通用公理；批量资源提取/创作仍留到RPG06第一版完成后另行授权。

交付：简洁收尾报告（变更、测试、风险、回退、剩余事项、是否发布），可核验机器状态与更新MANIFEST，下一轮含边界/小批门禁/验收/授权预算的独立计划。先交用户审批，再按明确授权处理PR；不自行合并。

## Latest evidence update (supersedes initial quota snapshot)

All8/8 Provider dispatches consumed; no remaining authorization. Gu3 turns, Minecraft1, original two-world six-turn gate incomplete. P30 applied and last3 calls structurally passed; no user quality acceptance yet. Consult RPG04_USER_REVIEW_RESULTS.json and latest status/ledger. Two Minecraft turns remain untested and require explicit additional quota or an explicitly approved acceptance-scope change. Do not waive this gap.

## Final available output update (supersedes earlier missing-turn snapshots)

User granted 2 additional dispatches, now10/10 consumed. Both new Minecraft turns passed structural validation, providing Gu3+Minecraft3. User quality approval remains pending, so do not mark full acceptance. First Gu turn is preP30, remaining five useP30; mixed-version coverage must be disclosed. Read RPG04_HANDOFF_INDEX.md and RPG04_CONTROL_PLANE_CALL_PATH.md, including CLI2048 versus test4096 differences. No further Provider calls authorized.
