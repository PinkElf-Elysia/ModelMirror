# M1 B2 — 装配与插件联动

B0/B1 已获用户确认；B2 实现及离线 HTTP 验证完成，等待确认进入 B3。
基线 61b6eea80152904eb8fb3cc0ebba5e7647594828，分支 codex/rpg-history-window。

## 本批交付

- M1 新会话设置 GET/POST、发送策略 revision、实际请求与回合策略记录已接通。设置、发送、模型切换和分支共用会话并发边界。
- 新增 history-dispatch、history-provider、history-branch-archive；沿用已有 Provider 传输、完整历史、原始回执和共享额度。插件不获得网络或派发能力。
- 控制目录等待期间明确停用会重新冻结完整历史；冻结后发生授权变更则拒绝发出旧请求。最后授权核对与同步网络请求发起共用插件队列，响应等待不持有队列，仍能停用或取消。已预留但未发出的预算保守保留，不自动重试。
- 分支完整保留分支点历史、继承该回合设置作为待用配置，不继承授权。未启用时完整历史发送；启用后才用窗口。父子设置互不影响，预算仍共享，来源请求 ID 不能重新执行。
- 设置结果未知时 GET 仅读取原操作；以同一 operationId 和完全相同 payload POST，可把未发布意图收束为 not-applied，不重新应用。确认结果后才允许新的明确设置操作。已有 complete 直接返回原记录。
- M1 宿主通过服务端 historyWindow:true 或 RPG_HISTORY_WINDOW_ENABLED=true 显式选择；默认 false，当前既有预览及数据目录不变。新运行版本为 format=3，旧会话无升级入口。

## 实际请求证据

B2-WIRE-EVIDENCE.json 登记本次8个 HTTP 场景的抓包文件 hash，以及主场景7次合成响应的消息角色、所选回合、开局资料补入状态和请求 hash。原始抓包与完整合成会话/回执保留在忽略目录 .rpg04-work/history-window-b2/http/。

主场景依次证明：零插件开局；窗口内第二轮；首轮移出后的第三轮；开启补入并切换模型后的父线；未授权分支完整历史；分支启用后窗口续玩；父线继续。每次实际传输的 messages 都与独立期望逐字及顺序对比；所有7份消息 hash 另从抓包重新计算，匹配持久化策略记录。正文包含合成 LSM 字段，原样保存和传输，不解析。

固定路由 fetcher 被本地截获；受控路由只请求 loopback 假服务。即使合成会话 mode=real、固定模型字段沿用正式配置，也没有访问真实供应商。真实派发0、额度授权0，不能据此判断内容质量或卡内记忆效果。

其他场景证明：同源/字段拒绝、设置失败恢复、目录等待撤权、传输边界撤权、取消迟到结果、未知 Provider 结果重启不重放、未知授权拒绝、分支幂等与设置隔离。额度耗尽仍可配置和创建分支，但不能继续派发；卸载重装不重置预算。

## 验证与边界

```powershell
node --test experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs
# 107 项：106 pass，1 skip，0 fail
npm.cmd test --prefix experiments/ai-rpg-engine/card-replica
# 26 pass，0 skip，0 fail
```

日志：.rpg04-work/history-window-b2/studio-plugin-tests.log、earth-tests.log。
唯一跳过为 model-legacy-copy.test.mjs：未提供 RPG_B3_LEGACY_COPY 指向实际旧会话副本，未触碰历史数据。合成旧格式兼容测试通过，不替代真实副本验收。
作者 prompts、原 assembly、原 model-runtime 与父市场页面4项基线 hash 完全一致。旧模型/分支核心文件未改；Studio布局与旧RPG05未改。正式UI、帮助中心、手机/键盘截图及两处生产构建属于B3，尚未运行。

## 失败历史

最初6项HTTP场景通过。增加最终授权队列时遗漏方法闭合符，8项场景第一次运行因语法错误未启动（http-guard.log）；补齐闭合符并执行语法检查后同组8项全部通过（http-guard-rerun.log）。之后补充初始配置来源核对及生成期间停用检查，最终全套106 pass / 1 skip。
证据汇总脚本首次用Windows默认GBK读取UTF-8抓包失败；显式UTF-8后成功，仅影响只读报告生成，未改变抓包或产品代码。历史日志和B1登记均保留。

## 交付、下一门禁与回退

B1原25份登记文件在任何B2修改前复制并逐项核对，保存在 .rpg04-work/history-window-b2/b1-snapshot；B1-STATUS.json另保留原状态字节。B1-DELIVERY.json未重写；当前源码登记采用B2-DELIVERY.json。抓包完整原文在本地忽略目录，随仓库交付的是可重跑测试、报告及hash索引。

本批没有Commit、Push、PR、共享部署或真实Provider调用，没有启动新持久预览。18453仍是B0已批准原型，不是B2正式界面。
优先停用插件恢复完整历史；保留会话、分支、设置及派发账本。当前合成数据目录不得交给旧宿主复用；降级必须先用副本验证。默认关闭M1服务选项不会迁移旧数据。代码回退仅撤回本轮增量，保留历史证据。
下一批B3须用户确认：正式UI设置流程、帮助、真实截图、两处生产构建及封装；不产生真实模型调用。
