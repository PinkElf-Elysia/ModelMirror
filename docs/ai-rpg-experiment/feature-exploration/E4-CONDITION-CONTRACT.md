# E4 条件原文合同补充与离线验证

2026-11-14。用户授权仅补充 E4 提案合同、保持现有校验、离线验证后提出最小补测。新增真实调用0、费用$0；不修改公共核心，不执行行动或部署。

## 改动

provider.mjs 的 CONTRACT 追加：

> condition.value非null时必须逐字摘录当前玩家输入中的连续原文，保留人称、措辞和标点，不做代词消解、概括或改写；quote也必须逐字摘录当前输入。例如玩家写“他已经答应了”，condition.value和quote应为“他已经答应了”，不得改成“周禾已经答应”。撤回条件时value为null。

原“玩家陈述仅为condition，不证明人物在场或同意”规则继续保留。JSON围栏解析、字段/引用/数量校验、E3条件校验、规则与状态计算均未修改。

## 验证证据

1. 实际组装的系统消息包含新合同且仅出现一次；玩家输入、已接受的第一版提案及其他材料与上次真实请求完全相同。
2. 将当前 provider.mjs 中新合同替换回上次冻结合同后，文件指纹与上次实测 manifest 一致，证明该文件只改了合同。
3. 已保存错误真实回复（gift、zhou、medicine、数量1，condition.value为“周禾已经答应”）仍报 INTENT_CONDITION_EVIDENCE，校验没有放宽。
4. 手写正例将条件值逐字设为“他已经答应了”：形成gift/数量1/对象zhou的仅意图草稿，preflight为空，旧接受清除，人物在场与同意仍unknown。此正例不是新模型回复，不代表Gemini已改善。
5. 原始真实回复、请求、运行声明、结果、统计、已接受草稿和源记录7项指纹未变。源库存仍5，所有状态未变。

证据目录：experiments/ai-rpg-engine/.rpg03-work/e4-condition-contract-offline-v1/，包含report.json、positive-fixture.json、proposed-request.json。后者仅冻结待确认请求，没有派发。

旧合同指纹 fe8e20d3a45ed0481744ed41040775679c72eaf334628ff45cbcd786fbbabd90。
新合同指纹 748fd35e32393bef8afe1068450cd4dbb3ed21cca1c12034611b18c243c6003c。

```text
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/check-condition-contract.mjs
node --test experiments/ai-rpg-engine/feature-exploration/context-actions-e4/*.test.mjs experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/*.test.mjs
```

离线证据9项核对通过；相关Node测试21项通过（含新增2项），不重复计算先前运行。证据脚本拒绝覆盖已经存在的目录。没有变更前端或运行中的预览进程，18903保留原补测失败事实。

## 最小补测方案（待确认）

- google/gemini-3.8-flash，既有选定连接及Google AI Studio标准端点。
- 最多1次，总费用不超过$0.20，不自动重试、不切换模型。
- 复用e4-fence-replay-v1中已接受的服药提案，不重新生成首轮；与上次补测相比只增加条件原文合同，保持输入与其他上下文相同。
- 唯一玩家输入：“改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。”
- 核对原始condition.value及quote均逐字为“他已经答应了”；gift、zhou、medicine、数量1正确；整批修订可以通过现有校验，旧接受和服药预检清除；只保留意图，人物条件未知，源记录及库存5不变。
- 确认后为新合同冻结独立运行声明、请求ID及单次预算，先完成模拟派发。旧实测脚本固定旧解析器/上下文指纹，不直接重跑，不放宽旧指纹门禁。
- 派发前重新核对路由资格、端点报价及预算；无法满足上限则停止。必要凭据只按授权加密暂存，结束清理，不修改共享配置。
- 保存原始回复、草稿差异、预检/规则边界和用量时间统计，提供独立只读页面。与原始失败和本次手写正例分开，未获确认不调用。
