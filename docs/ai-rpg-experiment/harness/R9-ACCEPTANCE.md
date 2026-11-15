# R9 离线验收结果
日期：2026-11-14。离线原型及后续真实原型均已获用户验收。下文保留离线阶段证据；后续实测见 R9-REAL-RESULT.md，当前收尾范围见 R9-CLOSEOUT.md。
基线：286de1eeb1066f1f82d265596e286373d872a49d；工作树 C:/tmp/modelmirror-rpg-scene-participants-r9；分支 codex/rpg-scene-participants-r9。这是离线阶段记录；当前已授权整理文档、Commit/Push/PR，仍不合并、部署或迁移。

## 实现范围
新增 agent-core/scenes/，不改原公共执行器或角色模块。
- 两种世界适配共享 SceneRuntime、任务登记和同一预算账本。
- 原创成年参与者模板用 AuthoringStore 发布版本、assembleCharacter 装配；场景内实例按世界与参与者绑定，不读取独立角色会话私有记忆。
- 场景对话需明确采用；世界记录原子保存采用记录，任务账本随后幂等投影。未采用、失效、失败回应不进入后续场景。
- query_scene_state 只读取冻结授权投影，保留原生工具编号和不透明签名；最终回应不得带状态增量。
- 现有 prepare_action / 确认执行 / 回执叙事继续由原世界宿主处理。角色没有世界写入端口。
- 检查点同时包含世界和采用对话；分支新建实例身份，继承检查点范围内的明确经历，不倒带共享预算。
- 独立 loopback 页面、固定验收输入、任务/查询依据、刷新和核对。不是 E6 情境建议或正式故事馆入口。

## 实际验证
工作目录 experiments/ai-rpg-engine；Node v24.18.0。
1. node --test agent-core/scenes/*.test.mjs：10/10 通过。
2. node --test agent-core/actions/*.test.mjs agent-core/actors/*.test.mjs agent-core/tasks/*.test.mjs agent-core/assembly/*.test.mjs agent-core/scenes/*.test.mjs：99/99 通过，0 跳过。
3. node --check agent-core/scenes/server.mjs；node --check agent-core/scenes/public/app.mjs：通过。
4. R9_PLAYWRIGHT_MODULE 指向本机已有 Playwright，node agent-core/scenes/browser-check.mjs：13 项通过，页面错误 0、非本地请求 0；桌面 1440×1000、窄屏 390×844 已检查截图，无横向溢出。

浏览器证据（非跟踪目录）：experiments/ai-rpg-engine/.rpg03-work/r9-validation/browser-sxdNVu/{result.json,desktop.png,mobile.png}。
测试数据在 .rpg03-work/r9-tests/；未读取共享存档。测试不调用提供商，不能证明 Gemini 行为、真实叙事或长期人设稳定。
本轮没有改前端构建入口或父项目接口，因此未运行完整前端/后端项目构建及全项目测试。

## 覆盖矩阵
| 检查 | 证据 |
| --- | --- |
| 灯塔交谈→采用→行动提案→确认→叙事→再查询→世界继续 | runtime.test + browser-check |
| 修仙同一参与者执行接口 | runtime.test + browser-check |
| 授权/未授权、未知值、无其他角色经历或秘密 | participants.test + recovery.test |
| 原生消息配对、工具签名、未知工具/增量/截断拒绝 | protocol.test + runtime.test + recovery.test |
| 只采用一次、提交后任务账本中断恢复 | recovery.test |
| 过期结果拒绝、不采用排除、同编号异内容拒绝 | recovery.test |
| 已保存未知回执，经关闭/新进程打开后核对，不增加请求 | recovery.test |
| 持续未知不允许绕过；续写失败不自动重试 | recovery.test |
| 世界状态与对话检查点一致，共用预算不回滚 | runtime.test + browser-check |
| loopback、Origin、入口白名单、无真实调用开关 | server.test |
| 旧行动/观察/任务/装配兼容 | 99 项回归中的原有测试 |

## 开发中发现并已修复
- 旧世界消息校验不允许角色 assistant 开场白：新增独立角色/工具消息约定，不放宽旧世界接口。
- 对记录对象键顺序的测试假设：按任务编号读取对应阶段。
- 时间线 kind 被采用记录的数据标签覆盖，导致人物发言误标为世界叙事：修正展示投影并加断言，浏览器重跑通过。

## 明确限制
- 固定两个原创参与者、固定宿主授权；没有导入已有角色私有会话，也没有动态招募/自主行动。
- 人物请求仅取最近 12 条自己的采用对话，世界续写仅附最近 12 条场景采用对话；完整记录仍保留。尚无长期记忆总结和精细 token 裁剪。
- 采用确认“某人说过”，不证明其主张真实；语义一致性依赖后续真实验收，程序只保证写入边界。
- 本轮只复用已合并角色模板与纯装配，不接待定世界接口、R3 候选/修订或 R4 裁剪工作。
- 有限离线 fixture 用于验证路径；不会按任意自然语言推演剧情。未测费用、token 或真实首字延迟。
- 存储遵循现有单目录所有权/CAS，未宣称任意断电耐久。原型未启用正式入口。

## 独立页面
数据：C:/tmp/mm-r9-preview/data。端口 18918，仅 127.0.0.1；服务 pid/stdout/stderr 在 C:/tmp/mm-r9-preview/。
- 从头操作：http://127.0.0.1:18918/?run=run-eede827759c0f0a8cdc1e5031b2a41a0fc97be1d
- 完整示例：http://127.0.0.1:18918/?run=run-3fc1f9ee8b4921da2f6e0b84c263fecb30ee2321
完整示例：灯油 5→3，2 条采用对话、2 段世界叙事、6 次模拟请求，实际费用 $0。已建立行动前检查点。
人工验收建议：交谈对象选沈砚→询问安排→采用→世界“添油 2”→确认→叙述结果→沈砚“查询登记余量多少”→采用→陆遥“刚才处理好了吗”→不采用→世界继续。刷新/检查点分支可在同页验收。
重启命令：
node agent-core/scenes/server.mjs --offline-preview --port 18918 --data C:/tmp/mm-r9-preview/data
只在原服务已停止、端口空闲时重启，不停止其他任务进程。不删除数据作为回退。若源码指纹变化需新数据目录，不能强行重绑旧记录。
