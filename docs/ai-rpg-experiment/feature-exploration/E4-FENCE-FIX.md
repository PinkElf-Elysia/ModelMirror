# E4 单一完整 JSON 围栏兼容与离线回放

2026-11-14。用户授权本次仅修复 E4 解析兼容，先使用已保存回复离线验证，再提出最小真实补测；本次新增真实调用 0、费用 $0，无公共核心修改、执行、部署、提交或 PR。

## 精确改动

仅 E4 provider.mjs 的 parse 输入层增加完整匹配：可接收裸 JSON，或单一完整的三反引号 json 围栏（小写 json 标记、LF/CRLF、外围空白）。围栏开头和结尾必须分别完整占行；不接受无语言标记/其他语言、前后解释、多个围栏或截断围栏。

仅创建局部解析字符串，原始 receipt.raw 不变。围栏内仍通过 JSON.parse、原有完整顶层字段检查、action 类型检查、E3 变更结构检查；随后照旧经过 E4 增量字段、对象、数量、引文与规则校验。没有宽松扫描正文提取 JSON，没有更改提示词或规则。

## 已保存真实回复的离线验证

- 来源：.rpg03-work/e4-context-real-v1/reply-1.txt。
- 原始回复指纹：b8e07ca3b31ec712ecbd1ba352f78c5e541b4701a896981121075bf8510edfad。
- 离线回放目录：.rpg03-work/e4-fence-replay-v1，独立草稿，原实测失败记录不变。
- 用原 source-state.json 及相同上下文核对后，仅从文件返回一次已保存回复，不导入供应商、凭据或网络调用器。
- 成功解析 use_medicine / medicine / 数量2；本地纯预检 5→3，真实库存仍5，源记录所有字段不变。
- 完整原始围栏回复在草稿存储及重新打开后保持一致；原始10项证据文件逐一核对未变。
- 为最小后续方案，在独立回放草稿中仅接受意图，并冻结 proposed-next-request.json；没有执行或派发。

证据 report.json、parsed-draft.json、preflight.json、accepted-draft.json、draft-record.json、proposed-next-request.json 位于回放目录。它们是修复后的离线派生结果，不替换原来的真实失败证据，也不算新真实验证。

## 验证

```text
node --test experiments/ai-rpg-engine/feature-exploration/context-actions-e4/*.test.mjs experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/provider.test.mjs
node experiments/ai-rpg-engine/feature-exploration/context-actions-e4/real/replay-fence.mjs
```

18项 Node 检查通过，其中新增4项覆盖围栏边界、严格字段、原始回复保存、持久化与只读预检。保存回复回放7项核对通过。回放脚本拒绝覆盖既有证据目录；复跑需另行指定或调整为新的独立目录，不删除本次材料。

不重启现有验收页；18889仍展示原实测停止事实。修复验证通过不代表第二次赠送修订已经真实通过。

## 最小真实补测建议（待授权）

复用此次离线回放并接受的第一版意图，不重新生成服药提案。仅新增一次 Gemini 请求：

“改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。”

拟固定 google/gemini-3.8-flash、既有选定连接、Google AI Studio 标准端点；最多1次，费用不超过$0.20，不自动重试或切换。授权后再补齐单请求ID与预算限制，先完整模拟派发，派发前核对报价和路由资格；不沿用旧脚本的2次额度或已用目录。

验收：原始回复行动gift、物品medicine、数量1、对象zhou；修订使旧接受失效，服药preflight清除；赠送仅意图，不变成use_medicine，不声称已执行；玩家答应陈述只作条件，在场和同意仍未知；源记录全量指纹和库存5不变。

新回复原文及围栏、解析后草稿、差异、预检/能力边界、token、费用、首次文本和完成时间均保存；与原实测和本次离线回放分目录并注明来源。成功后在独立只读页并列展示，不将复用的历史回复计为新调用。必要凭据按新授权加密暂存后清理，不修改共享配置。
