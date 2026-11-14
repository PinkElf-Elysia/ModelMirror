# E2 最终交接

**用户已通过本轮原型验收，并授权提交 PR。** 残余文字假设仅记录后续优化；不再增加补丁或真实调用。

阅读顺序：[收尾与证据](E2-CLOSEOUT.md) → 本文 → [离线验收](E2-ACCEPTANCE.md) / [首次真实结果](E2-REAL-RESULT.md) / [单次补测](E2-RETEST-RESULT.md)。
计划、修复记录保留过程中的授权状态，以本文与收尾记录为当前状态。

工作树 C:/tmp/modelmirror-rpg-free-action-e2，分支 codex/rpg-free-action-e2。
冻结实施与比较基线 e40f238cc71eeff4dcda267d4e07d46e18f915e1。代码提交及 PR 以 Git 和 PR 页面为准。

## 交付与复用

代码位于 experiments/ai-rpg-engine/feature-exploration/：
- free-action/proposal.mjs、fixtures.mjs、public/style.css：E1 原样复制，指纹见 E2-REUSE.json；E1 固定情境解释器不进入 E2 生成。
- free-action-e2/source.mjs：历史会话只读副本及可信启动参数绑定的 loopback GET 来源，版本/配置校验。
- context.mjs：提案适配 2.1，复用 R4 资料和公共上下文装配；移除正式正文、全量面板与状态增量职责。
- model.mjs / intent.mjs：结构、引用、有限意图依据校验；数量仅用于条件预估，未确认时保持 clarification。
- store.mjs：独立 SessionRecords，草稿修订、确认/撤销/取消、用户本地数量修订及交接导出。
- server.mjs、public/*：独立离线交互页面。
- *.test.mjs / browser-check.mjs / ambiguity-replay.mjs：离线与旧回复重放检查。
- real/、real-retest/：本任务专用的已授权实测脚本与只读审阅页，复用现有认证路由和出站安全检查；不是正式服务注册。运行会涉及凭据和收费，历史授权已使用完，任何再次派发需新授权。

没有修改公共核心、父项目桥接、原卡、主线/角色工作树或正式入口，不存在动作执行 API。

## 给主线的接口边界

| E2 内容 | 主线应如何处理 |
|---|---|
| modelmirror.rpg.e2-proposal/1 | 内部提案格式，不是公共动作协议或 prepare_action 的直接参数 |
| refs / requirements | 拟议对象与需求，存在引用不等于有动作规则或人物同意 |
| expected | 条件预估，绝不可作为状态补丁或 override 写入 |
| confirmation | 只表示用户接受此版本方案，不是执行授权 |
| source / revision / hash | 固定来源与修订证据；导入时须重读目标实例并重新核对 |
| modelmirror.rpg.e2-handoff/1 | executionStatus=not_executed，requiresExecutionValidation=true |
| HTTP GET 完整 view | 本地玩家提案上下文，不是角色认知权限接口 |
| real/ 审阅结果 | 冻结样例证据，不会自动成为已确认草稿或执行请求 |

E2 只读来源与草稿存储隔离。POST /api/draft 仅 revise/quantity/confirm/revoke/cancel/reconcile；GET /api/export 只导出已确认的待执行方案。

后续若对接公共执行，主线需要：明确目标实例与规则版本，读取当前状态，核对有效意图（包括用户本地修订）、权限、预算与人物条件，生成有类型的动作，再重新确认和结算。不能从文字或物资差额推断动作。

**赠送、邀请不等于自己服用/消耗。** 当前 E2 方案不可直接映射 R5 的 use_medicine 或 fuel_lamp。源历史副本不作为写入目标；进入新的实例需记录来源并确认。未适配能力保持不支持，不由 E2 扩写动作规则。

## 主线进度核对

本次收尾读取主会话最新消息：R5 两卡离线原型已由用户验收通过，正在制定 Gemini 最小真实补测方案；主会话报告 128 项离线测试及页面验证通过，E2 实际导出仅检查展示，赠送保持未执行。
这些是主会话报告，本分支未重跑其 128 项测试或验证其真实调用。E2 不修改 R5 公共执行器，也不替主线取得实测/部署授权。

## 验收与残余

- E2 离线 22 项及相关查询/装配回归 6 项；页面与旧真实回复离线重放均通过。
- 首次 2 次 Gemini 调用：$0.05746875；其中歧义预填失败证据保留。
- 修复后独立 1 次补测：$0.02815275；原始 refs/requirements/steps 为空，无容器推断，未触发宿主清空，源状态不变。
- 两次授权批次共 3 次真实调用，合计 $0.08562150，均无自动重试。额度不能继承或再次使用。
- 用户接受原型；“同门”“初识、关系与信任尚浅”的文字假设列为后续优化。不把合法 ID 或有限措辞校验当成完整语义正确性证明。
- profiles/mods 非空时拒绝，不声称已兼容任意插件组合。
- 实际浏览器使用历史副本；实时 GET 适配仅经受控 HTTP 测试，尚未与主线当前服务联调。
- 不声称长期稳定、S2 认知隔离或正式动作执行已完成。

## 运行与回退

离线交互：从工作树根运行 node experiments/ai-rpg-engine/feature-exploration/free-action-e2/server.mjs。
默认端口 18862、草稿目录 .rpg03-work/e2-preview/、来源 .rpg03-work/e2-source/session-1.json。来源需自行取得已授权副本并核对固定 SHA256；未提供则启动失败，不能改用伪造数据。普通单元测试使用原创夹具，不需要私有来源文件。

实时来源参数为 --source-url http://127.0.0.1:PORT/api/cultivation/sessions/SESSION_ID --source-session SESSION_ID；只接受 R4 修仙绑定及空 profiles/mods。当前 E2 不自动适配 R5 新绑定。

最新只读真实验收页 http://127.0.0.1:18869/，前次 http://127.0.0.1:18865/ 保留原貌。二者仅读非跟踪证据；干净克隆不附带这些证据，也不能据缺失文件自行重发收费请求。

撤下只停止已核对身份的本任务进程；保留草稿、来源及证据。没有世界状态写入，无需世界数据回滚。不停止其他服务、不删除共享存档。
本次 PR 不含合并、部署、正式入口接入或数据迁移授权。
