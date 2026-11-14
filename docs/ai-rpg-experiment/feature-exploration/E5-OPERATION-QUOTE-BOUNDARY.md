# E5 操作级 quote 澄清与离线验证

2026-11-14。用户要求仅澄清revise必需的操作级quote，不放宽校验或自动补写；先以保存回复离线验证，再提出补测方案。本轮没有真实调用、凭据读取、共享配置修改、正式执行、部署或发布。工作树codex/rpg-context-plans-e5，基线bfc3d748438637215530f66a50cdd4e4d915f852。

## 根因与变更

上次真实reply-1中第二个revise拥有changes[0].quote和condition.quote，却缺少自身op.quote。原E5 apply要求每个操作的quote为当前输入中逐字存在的非空文本，报E5_EVIDENCE符合既有合同。原提示用“revise增加stepId”简写，没有完整枚举revise字段，可能增加模型遗漏的机会；这属于解释，不声称已证明模型内部原因。

仅在provider.mjs的CONTRACT末尾追加：add/revise/remove各自必须有op.quote；revise完整结构为type、stepId、action、changes、quote及可选condition。action不变为null，changes无槽位变化为[]，仅变更或撤回条件时提供condition。只改条件也必须有op.quote。op.quote标明修改要求，changes[i].quote标明槽位值来源，condition.quote标明条件/陈述来源且等于value；三者分别校验，不继承，不自动补写，缺失整批拒绝。

SAMPLES、build、parse、Simulator、plan.mjs及E4全部保持原字节内容。没有将旧失败回复修改成合法回复。

## 验证

1. 从e5-revision-real-v1直接读取reply-1，复用e5-slot-real-v1已保存原计划和源快照重放，仍拒绝E5_EVIDENCE。原始对象、计划、源状态均不变，原saved仍有效。
2. 新增operation-quote-contract.test.mjs，按原字节冻结失败回复；验证缺失/空/null/非原文操作quote拒绝，内层quote不能替代；数量和条件quote仍分别校验。
3. 独立编写合法合成修订样例（不是修补供应商回复），验证撤回step-1、step-2数量1→2、保留ID/对象、player-claim原文、saved清空、旧预检不沿用、依赖待确认、世界不变。
4. 实际请求与上次冻结请求相比，只有system追加说明；user中的原计划、事实、输入全部相同。
5. Node新增5项通过；E5与E4递归测试合计60/60通过。318个冻结文件仅provider.mjs变化，且SAMPLES之后文本完全不变；原核心、E4及旧真实证据不变。临时指纹脚本首次因Windows反斜杠与预期正斜杠比较误报，统一文件路径表示后复核通过，未更改哈希或放宽产品校验。

命令（仓库根）：

```powershell
node --test experiments/ai-rpg-engine/feature-exploration/context-plans-e5/operation-quote-contract.test.mjs
$e5Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-plans-e5 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
$e4Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-actions-e4 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --test @e5Tests @e4Tests
```

证据位于experiments/ai-rpg-engine/.rpg03-work/e5-operation-quote-offline：provider-before.mjs、before-hashes.json、saved-response-replay.json、request-preview.json、tests.txt、integrity.json。新provider SHA256为71750fa92ab4e647359c377237503d7a7b3de18432dbfbe2c59efbcd28b87aa6。旧失败页面18928继续展示原证据，不把离线样例替换成真实结果。

## 最小真实补测建议（待授权）

仅1次google/gemini-3.8-flash，总计/单次≤$0.20，无重试或fallback。限定此前Google AI Studio标准端点，派发前再次核对资格及报价，不改共享配置；保留temperature0.7、top_p0.8、max_tokens8192、tools=[]、tool_choice=none。先跑完整模拟派发，再发送这一条。历史授权已消耗，不沿用余额。

复用e5-slot-real-v1原成功两步计划与状态，而非上次未通过修订。固定输入不变：

> 撤回第一步服药。第二步仍给周禾养息丹，数量改为两份。把第二步原条件替换为我的陈述：“他已经答应了。”这只是我的陈述，尚未核实。仍只拟方案，不要执行。

检查原始回复remove/revise各有操作quote、数量quote与条件quote分别合法；原校验直接通过，无补写或转换。step-1撤回、step-2仍为赠送medicine给zhou、数量2；陈述原文为player-claim，不改世界事实；saved清除，旧服药预检不沿用，顺序依赖待确认，丹药仍5且完整状态指纹不变。

保存新独立证据目录中的原始回复、冻结请求、计划差异、逐项结果、token/费用/首次文本/完成时间及派发/清理记录，不覆盖任何旧证据。必要临时凭据仅加密存储，结束清理；提供独立只读验收页。失败停止且保持旧计划，不自动修复、补调用或宣称连续修订通过。

本轮只证明合同说明和离线行为，不能证明Gemini会稳定遵守新增说明。后续调用等待用户明确授权。
