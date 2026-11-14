# R6 离线验收与独立页面

> 收尾更新：用户已通过本轮离线和真实原型验收，授权定位旧回归后提交 PR；不再真实调用、不部署。旧回执失败确认不是 R6 引入，见 [R6-REGRESSION](R6-REGRESSION.md)；最终交付与授权状态以 [R6-CLOSEOUT](R6-CLOSEOUT.md) 为准。下文保留阶段性记录。

> 2026-11-14 后续更新：离线原型已获用户验收；本次授权的有限 Gemini 接入已完成 3 次真实请求，实报 $0.00577725，临时凭据已清理。独立真实页面待用户验收，见 [R6-REAL-VALIDATION](R6-REAL-VALIDATION.md)。下文离线阶段记录保留；“尚无真实接入”等描述以该后续记录为准，未授权正式部署或 PR。

日期：2026-11-14。状态：离线实现完成，等待用户页面验收。没有真实调用，费用 $0；无共享配置、部署、提交或 PR。

## 范围与文件

新增实现位于 `experiments/ai-rpg-engine/agent-core/actors/`：

- tasks/host/composition：公共任务记录、世界与角色响应薄适配、独立组装。
- world-port/observations/context：卡片投影边界、角色授权查询、已合并角色上下文装配。
- fixtures/simulator：本地封存的原创成年角色、明确标识的离线固定生成器。
- server/public：独立 loopback 页面，没有正式入口或父项目接口改动。
- *.test.mjs/test-process/browser-check：确定性、协议、隔离、HTTP、实际进程中断和浏览器验收。

原 R5、修仙原稿、character/**、探索源码、父项目 package/lock 均未修改。本轮无需父前端构建或类型迁移；新增页面是原生 JS，语法检查和真实浏览器执行通过。

## 检查结果

| 检查 | 结果 | 边界 |
|---|---|---|
| 新模块定向套件 | 23/23 通过 | 世界 5→3 且其他状态不变；观察/库存权限分离；请求与查询无私有 canary；同模板多实例隔离 |
| R3/R4/R5 + 角色相关回归（含当时的 R6 22 项） | 120/120 通过 | 限定范围，不是角色全历史兼容或全项目测试 |
| 最终卡片映射边界调整后 R6 复验 | 23/23 通过 | 接口仍复用 R5 与 character/context；未改它们源码 |
| HTTP | 2 项，包含在定向套件 | 同源/请求字段检查，执行与观察去重、刷新不派发 |
| 实际进程中断 | 1 项，包含在定向套件 | 子进程终止后恢复原响应，供应商夹具新增生成 0；不等于任意断电持久性 |
| 独立页面 | 9 条流程通过 | 桌面 1440×1080、窄屏 390×844、下载、恢复和下一轮；页面错误 0，外部请求 0 |
| 浏览器源码 | node --check 通过 | 无 TS/构建产物变更 |
| 真实模型表现 | 未测试 | 不声称实际原生工具选择、人物稳定或正文质量已验证 |

最终浏览器证据目录（忽略目录，不入库）：

`experiments/ai-rpg-engine/.rpg03-work/r6-validation/browser-VwfqFB/`

包含 result.json、evidence.json、desktop.png、mobile.png。首次浏览器验收 browser-lEItt1 保留；随后修正新建权限标签及最新回复可见性，最终重放通过；随后统一新文件换行并再次重放，最终证据为 browser-VwfqFB。测试中修复了角色书夹具缺 extensions、HTTP 测试依赖对象排序的问题，没有放宽既有合同。

本轮运行时基线仍为 38266775；原型源码尚未提交。当前工作树文档与其原型源码一起交接，不将分支名视为已经发布版本。

## 复验命令

从 `experiments/ai-rpg-engine` 执行：

```powershell
node --test --test-concurrency=2 agent-core/actors/*.test.mjs
node --test --test-concurrency=2 agent-core/actors/*.test.mjs agent-core/actions/*.test.mjs agent-core/*.test.mjs agent-core/context/*.test.mjs character/session.test.mjs character/card.test.mjs character/store.test.mjs character/group/context.test.mjs character/group/foundation.test.mjs character/group/recovery.test.mjs character/group/host.test.mjs
node --check agent-core/actors/public/app.mjs
$env:R6_PLAYWRIGHT_MODULE='<已有 Playwright 包的绝对目录>'
node agent-core/actors/browser-check.mjs
```

第二条在最终源码上应为 121 项（比首次回归增加一条边界检查）；本记录保留实际执行的 120 项回归与调整后的 23 项定向结果，不伪造一次最终全范围运行。

## 页面操作

独立预览：`http://127.0.0.1:18891/`。默认需显式 `--offline-preview` 启用。本次进程 PID 54992；启动身份记录在 `.rpg03-work/r6-preview-process/`，只可在核对 PID 命令行后停止自有进程。独立运行数据在 `.rpg03-work/actors-r6-preview/`。

1. 默认“看见行动，不可查背包”创建场景；确认服用两份，回执应 5→3。
2. 点“刚才发生了什么？”看观察回应；点“还剩多少？”只能回答不知道登记余量。
3. 切换未获观察的对照实例，应没有上一个实例的历史或观察。
4. 新建“可查丹药登记”的场景，同样操作后可查询到 3；展开任务看原生调用和结果。
5. 点“接下来呢？”及玩家赠药转述，世界仍为 3；刷新不重执行。下载包含原请求和来源的离线证据。

页面没有任意文本模型入口，四种选择均是明示的固定样例。世界正文沿用 R5 夹具，人物正文为新的受限夹具，不把剧本演示当作真实文学性或人物自主性。

## 限制、回退与后续实测

- 只创建新绑定，不迁移旧角色会话；本次不实现身份档案共享、参与动态变化、私有记忆导入或长期认知抽取。
- 观察来自既有已确认动作；人物回复和玩家声称不会写入世界。任意自然语言事实审查仍不在范围。
- 观察投递是可幂等重建的投影；不是跨世界/角色记录原子事务。
- 真实派发、计费、自动备用、并行模型和 E3 行动执行尚未接入。本轮没有临时凭据。
- 未运行角色全套历史档案及全项目 UI 门禁；角色 R2 文档中的旧世界档案/帮助既有缺口并未因此关闭。
- 代码回退只撤销本轮独立目录与文档修改，停止核对过身份的自有预览；保留独立记录与证据，不通过删除存档回退，不停止其他服务。

页面验收后，如用户需要真实补测，再冻结有限接入和请求清单、次数与费用。历史额度不继承；当前不自动调用。
