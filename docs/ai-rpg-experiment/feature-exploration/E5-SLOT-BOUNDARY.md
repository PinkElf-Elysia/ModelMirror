# E5 提案槽位与规则参数边界：离线修复与补测计划

2026-11-14。工作树C:/tmp/modelmirror-rpg-context-plans-e5，分支codex/rpg-context-plans-e5，固定基线bfc3d748438637215530f66a50cdd4e4d915f852。本次仅授权说明澄清、已保存回复离线验证及补测计划；没有真实调用、凭据访问、公共核心/E4/共享配置改动、正式执行、部署或提交。

## 范围与实际改动

低风险，仅在provider.mjs的CONTRACT后追加说明，新增slot-contract.test.mjs和本说明/交接记录。parse、build函数、Simulator、plan.mjs、所有E4与E3函数、规则参数及桥接代码均不改。原实测请求/回复/计划差异/统计保留；补充说明不追溯改变旧结果。回退只需移除追加的合同段落，不需要迁移或回滚存档。

实际校验依据为context-actions-e4/draft.mjs的revise/derive、free-action-e3/intent.mjs的applyChanges和context-plans-e5/plan.mjs。说明区分两个数据层：

| 输出位置 / 行动 | 提案合同要求 |
|---|---|
| op.action | 行动ID，只能使用当前capabilities已声明的行动；add给出ID，revise不改行动时用null |
| use_medicine、fuel_lamp的changes | 仅item、quantity，每次最多2项，不重复 |
| gift的changes | 使用target、item、quantity，每次最多3项，不重复 |
| 每项changes | 仅field、value、quote三个键；item/target引用已登记ID，quantity是明确的正安全整数，quote保留当前输入证据 |
| 修订 | 只列实际变化；未列出槽位沿用，明确撤回才用value:null，不推断缺失值 |
| op.condition | E5单步条件放在此处，含kind/value/quote；不放进changes；没有条件null，修订未改时省略，明确撤回null |
| remove | 仅type、stepId、quote，没有changes或执行参数 |

capabilities.parameters是宿主规则输入声明，里面的actionId/itemId不是changes.field的别名。明确禁止把action/actionId、itemId、targetId、parameters等塞入changes，不能因为规则schema.required出现这些名称就照抄。宿主不会自动改名、删除错误字段或只接受部分结果。此为E5模型输出约定说明，不通过修改E4实现来改变字段范围。

## 已保存回复与离线验证

直接读取e5-condition-real-v1/reply-1.txt复现E4_CHANGES：use_medicine的changes为actionId/itemId/quantity三项，整批拒绝，状态不变。该原始回复逐字冻结到专项测试夹具并验证SHA256；没有把它转换为合法样例。另有独立人工合法样例用于验证允许字段，明确不冒充模型输出。

首次临时验证命令使用相对目录，被现有OFFLINE_ROOT_UNSAFE保护拒绝，未进入测试或模型派发；改用绝对任务目录后复现成功，未放宽存储检查。合同追加前后parse及后续所有函数内容一致，既有拒绝语义保持。

专项5项通过：
1. 原回复仍触发E4_CHANGES，不转换、不部分采用，来源状态不变。
2. 即使changes数量在上限内，actionId/itemId/targetId/parameters仍触发E4_FIELD或INTENT_FIELD；重复字段、嵌套参数及将E5条件塞入changes仍拒绝。
3. 独立合法两步夹具通过，首步只读预检5→3，赠送条件逐字保留且无执行/预检，实际仍5。
4. 实际请求只追加system合同，user消息、状态、能力参数及工具关闭配置保持原样。
5. 相同item/quantity槽位适用于已登记的灯塔添油规则；未登记的行动仍拒绝。

运行命令：
```powershell
$e5Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-plans-e5 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
$e4Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-actions-e4 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --test @e5Tests @e4Tests
```

49项通过（E5 27，E4 22）。未修改UI或桥接，本次不重启验收页、不重复父项目或浏览器检查。没有供应商模拟派发或真实调用；“离线测试”不是模型已理解新说明的证据。

证据目录experiments/ai-rpg-engine/.rpg03-work/e5-slot-boundary-offline/包含修改前指纹、provider-before、saved-reply-reproduction、tests、request-preview、comparison及最终integrity。旧真实页18922继续展示上次拒绝，18919保留更早的条件误判记录，未用离线样例替换。

## 最小Gemini补测（待确认）

- **最多1次，总费用不超过$0.20，不自动重试或备用切换。** 不继承先前剩余额度；确认后使用新的独立记录和请求编号，不覆盖旧结果。
- 模型google/gemini-3.8-flash，既有选定连接、google-ai-studio标准端点，temperature=0.7、top_p=0.8、max_tokens=8192、tools=[]、tool_choice=none。派发前重新核对端点资格和报价，按输入上界及最大输出预留，不满足上限即不派发。
- 先跑通单请求完整模拟派发，再发送唯一真实输入：**先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。**
- 使用原成年样例与相同5份养息丹/登记周禾，从空计划开始。只更新模型合同说明，不拿旧错误回复改名后当输入。
- 检查原始回复的服药changes只含item/quantity，赠送只含target/item/quantity；行动在op.action、原文条件在op.condition；不含actionId/itemId等错误字段，整批直接通过原有parse/apply。
- 形成两个有序步骤：use_medicine/medicine/2和gift/medicine/1/zhou；逐字条件“如果他不收，就不送。”类型prerequisite；首步仅只读预检5→3，赠送无预检，源状态完整指纹不变、实际仍5。
- 错误或结果未知即停止，不修补回复、不转换字段、不追加第二次。连续修订/撤回/玩家陈述仍不并入这次补测，不能据单次通过宣称全E5完成真实验收。
- 保存原始回复/SSE、冻结请求与合同指纹、解析结果/拒绝诊断、计划差异、预检、源状态前后指纹、实际token/费用与预算预留、首个文本和完成时间；未知与未测得独立标注。JSON提案没有叙事正文首字。
- 必要凭据仅加密保存在本任务新临时目录，结束清理。新独立只读页面供验收，旧证据保留；无共享配置修改、正式执行或部署。当前未实现或运行下一次派发入口，等明确授权再补齐。

## 授权执行更新（2026-11-14）

用户随后授权本方案单次/$0.20补测。完整模拟1次及唯一真实请求均通过，费用$0.00528，无重试；原始字段合法、条件逐字保留、两步通过原校验，无自动转换，状态不变，凭据清理。本次待用户18925页面验收，连续修订场景仍未真实验证。详见E5-SLOT-REAL-RESULTS.md。本轮额度已使用，不自动追加请求。
