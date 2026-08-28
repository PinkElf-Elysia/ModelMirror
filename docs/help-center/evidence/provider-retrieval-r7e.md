# R7E OpenRouter Batch 帮助增量重放记录

## 基线与隔离边界

- 验证日期：`2026-08-27`。
- 最新主线：`origin/main@821067a7db4811a3f3f1fd649e4fdfade9eafb22`。
- 交叉验证工作树：`C:\tmp\modelmirror-r7e-latest-check-20260827`。
- R7E 实施基线：`4bfef53c4b32f3fa8044122553c7a8f42bd08908`。
- 本次把完整 R7E Diff 应用到最新主线临时工作树，没有文本冲突；实际 R7E 分支未 rebase。
- 最新主线前端从该临时工作树重新执行 production build，并由只绑定 loopback 的静态服务
  提供于 `http://127.0.0.1:15350`。它不加载 `.env`、Provider Key 或 Router 数据。
- 原计划启动全新的 Docker 网络和无密钥后端，但 Docker 地址池已耗尽；没有删除、清理或
  修改任何现有网络。故本记录证明最新主线的前端与帮助任务，不证明最新主线完整后端预览。

## 用户路径重放

在应用内预览器中完成以下只读路径，没有填写输入、点击提交或调用上游：

1. 打开 `/chat/openai%2Fgpt-5.6-luna?serving=batch`。
2. 页面显示“异步批处理”和 `OpenAI: GPT-5.6 Luna`，初始有效输入数为 0，最多 100 条。
3. 两个文本框均为空，“提交批处理任务”保持禁用；切换到 Batch 本身不会产生调用。
4. 侧栏显示模型 ID、输入/输出价格、最长约 24 小时，以及文本、异步、供应商保留和
   `billing_authoritative=false` 数据边界。
5. 打开 `/help/check-availability-cost-data`。
6. Batch 帮助明确防重语义仅属于 Managed 路径；请求标识不是凭据；刷新失败应等待每 5 秒
   自动查询恢复；legacy 直连不提供相同保证；创建新任务可能再次收费并需要重新确认。
7. 页面控制台 warning/error 为 0。

本次没有新增截图：变更只补充现有解释文章的文字边界，入口、按钮或页面布局没有变化；文章
原有截图继续保留其 `cc49136c` 基线，不把旧图片复制后冒充最新截图。该文章仍按全篇既有图片
基线显示 `cc49136c / 2026-08-26`，本文件只证明 2026-08-27 的 Batch 文字增量重放。

## 自动验证

在最新主线加 R7E Diff 的临时工作树执行：

```powershell
cd client
npm.cmd run test:run -- OpenRouterBatchWorkspace.test.tsx ProviderWorkloadControlSettings.test.tsx helpContent.test.ts HelpCenterPage.test.tsx
npm.cmd run typecheck
npm.cmd run build
```

- Batch、Settings 与帮助专项：4 个文件、40 项通过。
- typecheck 通过。
- 修改后的 production build 通过；仅保留仓库既有的大 Chunk 告警。
- 帮助目录测试曾正确拒绝把带旧截图的整篇文章直接标记为新基线；撤回该错误标记后，原测试
  重新 40/40 通过。没有修改测试来放宽截图来源校验。

## 与真实 Batch 证据的关系

- R7E 实施预览 `127.0.0.1:15150` 已显式固定到本轮后端。通过该前端代理只读查询此前已获
  授权的本地任务，状态仍为 `completed`，请求总数 1、成功 1、失败 0。
- 本次收尾不新建 Batch、不重新认证、不重跑付费调用；最新主线静态预览也不连接 Provider。
- 查询恢复的成功→瞬时失败→成功和首次恢复失败→成功由组件 fake-timer 测试证明；本轮未
  人为中断真实上游来制造浏览器故障。

## 未验证边界

- 没有在 `821067a7` 上启动隔离后端、执行真实提交、认证、重启恢复或上游结算。
- 没有执行最新主线完整前后端全量；针对性交叉验证结果与实施基线全量结果分开报告。
- 没有重新验证文章中与 Batch 无关的旧截图任务，因此不更新整篇文章的全局截图基线。
- Docker 地址池耗尽属于环境阻塞，不是 R7E 源码失败；发布前若主线继续变化，仍需再次刷新
  基线并按相交范围回测。
