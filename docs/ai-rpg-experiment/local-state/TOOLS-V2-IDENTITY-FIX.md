# 原型修复：角色身份与单回合状态约束

## 范围和结果

本次没有真实调用、凭据读取、部署、共享配置修改、Commit、Push 或 PR。保留此前 Gemini 的原始失败回复、原状态及验收页面，不把离线合成回复替换为真实结果。

修复限于现有本地状态模块：

1. v2 每次请求追加精简的已登记人物名字／ID 索引，不传完整关系面板。周禾即使没有在当前输入中出现，也明确对应 `zhou`。关系详情仍按需查询，工具返回不重新触发世界书。
2. 明确合法 ID 字符及复用规则；v2 对非法 ID 报 `STATE_ENTRY_ID`，对已有 ID 或同名重复登记报 `STATE_RELATION_ALREADY_REGISTERED`。同名比较仅 NFKC 和首尾空白规范化，不做模糊角色合并；真正新角色仍可登记，既有角色正常关系更新仍可使用。
3. 为这次已明确的服药动作增加可选 `quantityOnly: {id: "medicine", delta: -2}`。该约束写入请求提示与冻结的回合记录，提交和恢复都校验；必须只有这一项数量变化。改关系、增加角色、改其他物资、改变数量或增加补偿操作均整批拒绝，报 `STATE_CHANGE_SCOPE`，不静默删掉违规项后提交。

第三项是明确动作的宿主约束，由调用方传入，而不是自动理解任意自然语言的解释器。只有普通聊天输入“其他状态不变”而未绑定 quantityOnly 时，提示词会要求遵守，但不能声称已有程序级的任意语句约束。本次补测已将该选项明确冻结，保证除 medicine 数量之外的本地字段不发生变化；正文语义是否遵守仍需真实生成与人工验收。

第一轮默认状态规则及原稿不改。严格关系检查由新版 v2 上下文启用；旧记录没有 statePolicy 时保持原解释。工具合同指纹已改变，使用新会话验证，不静默迁移旧 v2 会话。旧实测只读页继续保存旧证据，不作为新合同的续写入口。

## 离线验证

针对性 21 项测试通过；随后本地状态及原生工具全部 57 项离线测试通过（包含前述用例，不重复累计）。

```powershell
$tests = @(Get-ChildItem experiments/ai-rpg-engine/local-state -Filter '*.test.mjs'; Get-ChildItem experiments/ai-rpg-engine/local-state/tools -Filter '*.test.mjs') | ForEach-Object FullName
node --test @tests
```

覆盖非法 ID、同名及同批重复角色、合法新角色、正常关系更新、精确数量约束、额外关系变化拒绝、回合幂等、未知结果恢复仍使用冻结约束、原稿指纹、旧模式、面板、存储和原生调用桥接。没有修改父项目桥接或前端文件，本次不重复执行父项目全量测试和前端构建。

对保存的真实回复离线重放：

- 单独启用新版身份检查，原 `zhou_he` 返回正确的 `STATE_ENTRY_ID`。
- 启用本次数量约束，原有八项增量返回 `STATE_CHANGE_SCOPE`，不入账。
- 明确标注的合成合法回复只提交 medicine −2，丹药 5→3，其他字段整体深比较相同。
- 原查询结果重新从冻结初值读取，指纹相同。没有改变真实失败记录或重放模型请求。

非跟踪证据：`experiments/ai-rpg-engine/.rpg03-work/tools-identity-fix/`，包含 offline-proof.json、冻结 context/request/initial 和 plan.json。原回复指纹 `d105c1cdb3a0718f865daf203bf68fb75189ace10eaf04c6faa3e158b16a6533` 保持不变。

## 最小真实补测方案（待用户确认，尚未调用）

仅 **1 次 Gemini 最终续写**，不再重复已经验证成功的自主查询阶段。

- 模型：`google/gemini-3.8-flash`，沿用已选连接，限定 Google AI Studio 标准端点；不更改共享配置。
- 输入：原成年开局和原用户动作，新增本次身份索引及明确数量约束；复用之前已保存的原生 assistant 查询消息，并从相同冻结初值重建已验证一致的 tool 结果。
- `tool_choice: none`；temperature 0.7、top_p 0.8、max_tokens 32768。无自动重试或修复调用。
- 上限：1 次，单次及总计 **USD 0.60**。旧报价下保守预留约 USD 0.529；授权后重新核对公开报价及实际完整请求预算，不满足即停止。
- 验收：只返回 medicine −2；如引用周禾则复用 zhou，不新增同名角色；关系及其他字段完全不变；正文与结果一致。失败则保留原回复、整批不提交，不追加请求。
- 保存原始回复、工具上下文、用量费用及时间；清理临时凭据，提供新的独立只读页面。不部署。

这次一请求方案验证修复后的最终生成，不重新验证模型主动选择查询或完整两请求延迟。原始真实链路与本次新结果分开记录。

## 文件与回退

- `local-state/tools/adapter.mjs`：人物索引、ID 提示、数量约束与状态策略。
- `local-state/tools/host.mjs`：接收并冻结约束、重复操作校验。
- `local-state/rules.mjs`：可选严格关系检查及精确数量范围。
- `local-state/store.mjs`：提交使用回合冻结策略。
- `local-state/tools/identity-scope.test.mjs`：聚焦离线回归。

未部署，无需共享栈回滚。回退代码时保留新旧记录及其绑定配置；不删除存档，也不把违规回复转为部分结算。
