# E2 歧义修复与最小补测方案

状态：离线修复完成；真实补测待授权。本轮真实调用 0 次，没有读取或创建临时凭据。

## 原因与修复边界

原校验检查 ID 存在、数量为正整数及库存是否足够，没有区分“登记中存在”与“玩家明确指定”。因此旧回复一边问数量，一边填 quantity=1，仍能产生条件预估。

本次只改 E2：
- context.mjs：提案适配 2.1；缺失对象或用量先澄清，refs/requirements/steps 空数组；禁止推断丹药位于布囊等未登记关系。NPC 在场/同意作为世界条件，不能代替玩家确定对象、数量。
- intent.mjs：保守核对明确名称、界面选择及紧邻物品名称的明确数量。未匹配或冲突时保持未知，不从唯一登记对象或可用库存推默认值。
- model.mjs：缺少意图依据或出现已覆盖的容器取放描述时，整个行动保留为 clarification；清空执行步骤、绑定引用、用量及条件预估，附澄清诊断。原始回复保存在 receipt，不篡改。
- store.mjs：保留用户主动本地修订用量，作为宿主确认的数量来源；模型/输入 JSON 不能伪造该来源。其余不明确字段仍不能因此变成已确认。
- ambiguity.test.mjs / ambiguity-replay.mjs：新回归与真实坏回复离线重放。

没有修改公共核心、规则引擎、共享配置或正式执行；未升级/迁移旧草稿。旧真实验收页面 18865 继续展示历史实测原貌，不冒充修复后的新生成结果。

## 已验证

1. 22 项 Node 测试全部通过（原有 14 + 新增 8）。
2. 重放上次两份真实原文：
   - 明确请求仍引用 medicine、zhou、数量 2，条件 3→1。
   - 歧义坏回复变为 clarification，refs/requirements/steps/expected/basis 均为空；原始回复原样保存。
3. 浏览器重放验证：确认与导出按钮禁用，服务端同样拒绝；无数量编辑或扣减预估；刷新不重新生成。
4. 既有 6 组浏览器流程通过，包括正常确认、数量修订、源状态变化、桌面及手机布局。
5. 源状态 SHA256 与原实测证据完全不变，实际丹药仍为 3；重放没有连接模型网关。

命令（工作树根）：

```text
node --test experiments/ai-rpg-engine/feature-exploration/free-action-e2/ambiguity.test.mjs experiments/ai-rpg-engine/feature-exploration/free-action-e2/core.test.mjs experiments/ai-rpg-engine/feature-exploration/free-action-e2/integration.test.mjs
node experiments/ai-rpg-engine/feature-exploration/free-action-e2/ambiguity-replay.mjs <existing-playwright-directory>
node experiments/ai-rpg-engine/feature-exploration/free-action-e2/browser-check.mjs <existing-playwright-directory>
```

非跟踪证据：
- .rpg03-work/e2-ambiguity-offline-PX0A8V/result.json、desktop.png、mobile.png
- .rpg03-work/e2-validation/browser-2yf3bz/result.json 及截图（实际文件名以目录为准）

## 限制

名称匹配、少量中文/阿拉伯整数表达和常见容器取放描述是有限原型校验，不是完整语义理解或事实审查。别名、省略、复杂数量表达可能需要明确重述；未覆盖的自然语言措辞不能宣称已完全阻止。容器规则首先写入模型合同，本地只对常见违规表达做保守拦截。

这轮证明的是“已有错误输出不会变成有效绑定和用量”。新合同能否让模型原始输出本身不再猜测，需要下面一次真实补测。不能用本地清空后的结果掩盖模型仍猜测的问题。

## 最小补测（待用户确认）

- 模型：google/gemini-3.8-flash。
- 路由：沿用已验证的 Google AI Studio 标准端点，经现有认证路由；不修改共享配置、不切换备用。
- 次数：最多 1 次；每次及总费用上限 $0.20；不自动重试、修复生成或追加澄清轮。
- 参数：temperature=0.7、top_p=0.8、max_tokens=8192；tools=[]、tool_choice=none。
- 输入原样重复第二个用例：“帮我给那人一些丹药，先想想怎么安排，暂时不要动。”
- 无预选对象、无历史模型回复；同一冻结状态（丹药3、周禾已登记），采用新提案合同。
- 派发前用新的独立运行目录/单次额度核对完整模拟派发和路由报价；不复用 v1 运行目录或两次授权。若预算预留不能满足 $0.20，不派发。
- 原始输出验收：主动询问对象、物品及数量；refs/requirements/steps 为空；不默认周禾、数量1、容器关系，不宣称行动发生。
- 宿主验收：保持 clarification；没有绑定用量或预估；源状态不变。
- 若原始输出依然预填，即使本地成功拦截，也记“模型原始输出未通过”；保存结果停止，不自动补测。
- 保存冻结请求、原文、校验前后提案、token/费用/首次输出/完成时间，单独标注新的真实结果并更新验收页。
- 如需凭据，仍仅在任务临时目录加密保存，主密钥只在内存传递，结束清理。正式执行、部署、提交、PR 均不在范围内。

上次同类请求实报约 $0.029，仅作为历史参考，不作为当前报价或固定费用承诺。

## 单次真实补测完成

已按后续授权完成 1 次 Gemini 补测，实报 $0.02815275，无重试。原始 refs/requirements/steps 为空，无容器推断，未触发本地清空；状态未变。仍有同门、初识关系的文字假设，记录待优化。详见 [补测结果](E2-RETEST-RESULT.md)。新只读页面 http://127.0.0.1:18869/ 待人工验收。临时凭据已清理，未改共享配置或接正式执行。
