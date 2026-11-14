# R5 离线验收记录（分批历史）

2026-11-14。冻结基线 d8ead08baf7a072ab3e85e683634d1e98d8ef233。独立树 codex/rpg-actions-r5。
本文件主体保留离线批次记录。后续用户已通过离线及真实恢复补测原型验收；最终状态和边界见 [R5-CLOSEOUT.md](R5-CLOSEOUT.md)。下文未真实调用／待验收描述均指当时批次，不代表当前状态。

## 已覆盖

- 两卡共用查询、准备、确认、执行、叙事链；丹药／灯油 5→3，其他结构状态保持。准备和取消不改世界。
- 正整数、已知数量、库存不足、越界／溢出、耗尽档案保留；旧版本方案、内容篡改及同操作不同内容拒绝。
- 同编号重复返回原记录；并发只允许一个执行。执行前后故障注入、关闭重开、未知结果核对，均不重复扣减。
- 已执行但叙事失败／截断／携带非法增量时保留回执。仅恢复叙事，或明确结束本轮；未叙述的行动仍进入下一轮依据。
- 方案工具结果已保存但后续中断，恢复直接采用准备结果，不重复生成。查询与续写对应 ID、冻结前缀及不透明元数据保持。
- 不明工具、坏参数、额外调用、截断拒绝，无自动重试。模型增量不能再维护本轮受管数量。
- 停用保留资产；显式改写留原因；检查点分支保留状态且不修改源记录。旧提案不能跨分支执行。
- 修仙实际前后置词、历史世界书触发和正则仍装配；总结／记忆只投影正文与已提交变化，不将工具消息当历史或账本。
- E2 实际导出只读验证通过；赠送缺少类型化动作映射，返回未执行，不作错误兼容。

## 命令与结果

在 experiments/ai-rpg-engine 下：

```powershell
node --test --test-concurrency=4 agent-core/*.test.mjs agent-core/context/*.test.mjs agent-core/context/real/provider.test.mjs agent-core/real/provider.test.mjs local-state/*.test.mjs local-state/tools/*.test.mjs agent-core/actions/*.test.mjs
node --check agent-core/actions/public/app.mjs
node --check agent-core/actions/server.mjs
```

初次组合回归 126/126；随后补充本轮插件及只保留回执连续性用例，本轮 actions 定向测试 23/23。最终组合回归结果见本文件末尾交接更新。旧回归与新模块数量不得重复相加。

浏览器脚本 agent-core/actions/browser-check.mjs 使用本机已有 Playwright + Edge，不新增依赖；启动临时端口与独立目录，阻断非本页网络，结束只关闭脚本自己的实例。
设置 R5_PLAYWRIGHT_MODULE 指向已有包目录后执行 `node agent-core/actions/browser-check.mjs`。

首个浏览器样例发现前端随机操作编号可能以数字开头，已加 op- 前缀符合宿主格式。修复后 browser-hLxm6H 通过：修仙查询提案与 5→3、明确失败、刷新、未知回执核对、下一轮、灯塔同链路、390 像素宽度无横向溢出；页面 JS 错误 0、外部网络请求 0。桌面与窄屏截图已查看。
该目录在 .rpg03-work/r5-validation/，截图及全部模拟记录不入仓库。

## 测量与证据

每阶段保存输入哈希、字符数、系统／历史／工具声明／工具结果／整包字节，模拟原始回复及 SSE、工具结果、实际变更和完成耗时。人工再次生成保留 previousAttempts，次数不重置。
离线费用 $0，真实调用 0；token、可靠正文首字未测得。模拟耗时不作为玩家真实等待门槛或费用改善证据。

## 预览与剩余缺口

独立验收入口 http://127.0.0.1:18871/；启动命令与步骤见 actions/README.md。使用自身 .rpg03-work/actions-r5-preview，不操作共享栈。
范围只在实验 actions 模块及本轮文档，未改父项目、生产前端、原稿、其他工作树或依赖。因此不运行无关父前端构建或全项目后端测试；静态页面有语法与浏览器验收。

尚未验证：Gemini 原生选择和真实续写、任意自由输入准确性、完整主平台路由资格与预算、真实费用和延迟、角色私有认知、多角色裁定。R5 不开放候选模型对比；旧模式相关回归保持。故障注入和重开不是任意断电持久性保证。

回退仅停止本轮独立进程，保留版本匹配的数据和证据；不删资产，不自动迁移或重放。不提交 PR、不部署。待用户离线验收再另行决定下一步。

## 最终交接更新

最终代码组合回归 **128/128**（原有相关 105 项＋本轮 23 项），全量输出位于 .rpg03-work/r5-validation/final-tests.log；无失败或跳过。最后浏览器记录为 browser-AjWxQq，重复覆盖同一完整链路，JS 错误 0、外部请求 0。阶段字节和尝试摘要见 [R5-OFFLINE-SUMMARY.json](R5-OFFLINE-SUMMARY.json)。

独立预览 18871 已启动并 GET 核对 HTTP 200，bootstrap 明确 offline，包含 cultivation/lighthouse。进程 PID 20752，PID 文件位于 .rpg03-work/actions-r5-preview/preview.pid。页面服务启动后未执行任何真实调用。未来重启 PID 可能变化，关闭前须核对进程路径与本任务目录。

Git 最终检查仅新增 actions/** 与 R5 文档，既有跟踪文件无差异；未暂存、提交或发布。页面待用户人工验收。
