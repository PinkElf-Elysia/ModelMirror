# R5 保存提案恢复补测

日期：2026-11-14（用户时区）。用户授权有限恢复接入、完整模拟、复用已有有效工具调用确认执行一次，以及 Gemini 正文补测最多 1 次、总费用不超过 $0.30。不自动重试，不修改共享配置、部署或提交 PR。

## 实现与来源

- 新增 actions/real/recovery.mjs、recover-run.mjs 及针对性测试。原生提案从前次 stage-1.json 的原始 SSE 重新解析，不重新生成、不手填动作覆盖状态。
- 固定源文件 SHA256：4b0d9d80b0585ce10e91e68564e74aaae172e34a3e33a126a8fd7c1a31aea889。
- 校验源初态、旧会话无执行事件、当前适配绑定（仅运行代码 manifestHash 允许更新）及原生工具参数。调用是 prepare_action / use_medicine / medicine / quantity=2。
- 独立测试会话经正常 ActionHost 工具执行器生成新会话绑定的提案，由脚本检查后确认并执行一次。旧 unknown 回执及原会话没有修改。
- 单次正文授权拒绝计划阶段派发，使用原叙事配置 god-long-v1，tools=[]、tool_choice=none，最大输出 32768。计费派发最多一次，预算上限 $0.30。
- 沿用独立桥接、路由资格、凭据保护和出站校验，没有新增正式接口或依赖。
- 验证的是“保存的真实原生回复恢复→确认执行→一次真实正文”。它不等同于重新验证一条从首次生成开始、不中断的真实调用链，也不涉及角色工作树集成。

## 先行离线验证

完整 mock 恢复派发通过：计划结果来自保存回复，上游只收到一次模拟正文请求；丹药 5→3，其他状态完全一致，执行事件和已提交正文各一条，刷新读取一致。临时凭据已删除。

本批命令与结果：

    node --test experiments/ai-rpg-engine/agent-core/actions/*.test.mjs experiments/ai-rpg-engine/agent-core/actions/real/*.test.mjs
    # 28/28 通过
    python -m pytest experiments/ai-rpg-engine/agent-core/actions/real/test_protocol.py -q
    # 8/8 通过

预算测试确认计划阶段不会派发、同请求不会重发、第二次正文请求会被拒绝。完整模拟还通过实际路由资格和出站校验，仅上游生成内容替换为 MockTransport。

## 真实结果

| 项目 | 结果 |
|---|---|
| 模型／端点 | google/gemini-3.8-flash / Google AI Studio 标准端点 |
| 新增请求 | 1 次，槽 3 正文阶段；计划阶段 0 次 |
| 预算预留 | $0.219440，低于 $0.30 |
| 实报费用 | $0.03974325；整微美元记录向上取整为 $0.039744 |
| 输入／输出 token | 37146 / 3169 |
| 输出包含的推理 token | 793；不重复累计 |
| 首次可见文本 | 16.365 秒，宿主每 250ms 观测 |
| 正文首字 | 未单独测得，保留 null，不冒充精确首字 |
| 请求完成 | 46.235 秒 |
| 完成标志 | stop，桥接 completed，上游审计 succeeded |
| 执行 | 确认一次，行动事件一条，丹药 5→3 |
| 其他结构状态 | 与初态逐字段一致 |
| 后续状态 | 已提交正文一条，无 pending，刷新读取一致 |

前次调用费用 $0.029112；两批累计 $0.06885525。原始失败与本次补测分别记录，不能把恢复重放算成新的模型调用或丢掉前次费用。

正文明确写了“两枚丹药送入口中”，没有要求再次扣除物资，没有输出 state_delta 或 variable_panel。余额 3 由本地结果展示，正文未直接报剩余数。未新增关系记录、余额或能力变更。

## 残余叙事问题（未修补）

- 补写“昨日打理药圃、搬运灵泉水留下的筋骨酸乏”等未确认过去经历；初始化记录为灵力充盈、精神充足，不能把这段补写视为经验证的历史。
- 仍输出世界信息、当前角色、世界动态、星记忆回廊及选项，与本阶段不输出面板的要求有偏差。
- 自行补充半个时辰、日期、杂务面积等；这些不是宿主确认的状态变更。正文和伪记忆面板没有反写权威数据。

因此可确认执行与数量一致性、结构状态不变；不能宣称正文全面遵守“其他状态不变”或所有叙事约束。保留原文供用户决定原型容忍度，不追加补丁或收费调用。

## 验收与证据

独立只读页面：http://127.0.0.1:18875/ 。原失败证据页面 18874 保留。新页面已验证 HTTP 200、正文与费用可读、保存工具可展开、刷新无调用、无脚本错误及无派发按钮。

非跟踪目录：

- experiments/ai-rpg-engine/.rpg03-work/actions-r5-real-recovery-v1
- experiments/ai-rpg-engine/.rpg03-work/actions-r5-mock-recovery-v1

保存 execution-claim、recovery-source、recovered-tool、confirmation、executed-session、最终 session、stage-3、reply-3、完整 SSE、dispatch-audit、cleanup，以及独立 assessment 和浏览器检查／截图。审计报告与原始数据分开。

真实和模拟目录的 router 加密快照、local-auth 均已删除，主密钥没有落盘；两个桥接进程均已退出，仅只读页面继续运行。未修改共享配置、正式前端或探索／角色工作树。无 Commit、Push 或 PR。

当前状态：用户已通过本轮原型页面验收，残余叙事问题作为后续优化项；不再补丁或真实调用。已授权整理文档并提交 PR，不部署。见 [R5-CLOSEOUT.md](R5-CLOSEOUT.md)。
