# M1 B1 — 策略与宿主交付

状态：B0 已由用户「确认」批准；B1 离线实现及验证完成，等待用户确认进入 B2。
基线：61b6eea80152904eb8fb3cc0ebba5e7647594828（PR #383 合并）；分支 codex/rpg-history-window。

## 已实现
- `plugins/history-window.mjs`：无 I/O 的审核提案及纯选择策略。默认 3 回合，1–50 整数，开局资料默认 off，严格拒绝多余配置字段。完整回合复制；开局原始快照只在明确开启且首轮移出时前置一次。卡内记忆不解析、不另行注入。
- 审核清单加入第三插件与精确制品 hash；权限仅完成历史、配置提案、UI。沿用安装/会话启用、版本/权限核对、卸载撤权及提交票据，不新增模型或网络能力。
- 新 `HistorySessionStore` 创建 format=3 新会话；绑定策略、装配器、新存储、原作者文本、卡片和模型配置。原 format=1/2 宿主源码不改，旧会话不追认 M1。
- 配置和操作回执存入会话的 `historyWindow` 可选字段，保存独立配置 revision；成功保存同时推进 session revision。全历史、模型选择和参数不变。
- 设置先在同一会话锁和插件提交队列内持久化意图，再原子发布新配置。最终落盘失败保留旧配置及 pending 意图；重启后仍阻止换操作 ID、发送准备和其他修改。用原 operationId 查询恢复为 not-applied；该旧 ID 不会再次应用。已发布但返回丢失则恢复 complete，不重复保存。
- 授权状态与配置 revision 一起形成有效策略 token，停用/重装/重新授权均改变 token。过期 token 拒绝；未知授权不回退到另一种装配。
- 请求准备使用真实会话进度和完整历史执行现有作者/世界书装配，然后只替换历史范围。输出独立策略记录含所选回合/请求ID、补入状态和最终请求 hash；技术信息不进入消息。

## 本批明确边界
当前 `studio/server.mjs` 尚未切换到新存储；正式 HTTP、M1 发送、分支与模型切换联动属于 B2。
新存储显式拒绝 format=3 的发送和分支，避免落入旧装配链。它是 B1 可审阅的宿主基础，不是可游玩的 M1 服务。
`prepareHistoryRequest` 验证的是宿主请求准备，不是实际 Provider 派发捕获。每轮策略记录落入实际请求/回合记录也在 B2 完成。
内部 `recoverHistoryOperation` 会进行原子状态收束；B2 暴露 HTTP 恢复入口时必须经过同源写操作检查，不能把它作为无保护的 GET 写操作。
原型 URL 18453 仍为获批静态演示，不连接本批存储。UI 集成、帮助中心和两处生产构建留 B3。

## 验证
Node v24.18.0。仅在独立模块按原 package-lock 执行 npm ci --ignore-scripts，无依赖版本/锁文件变更。

```powershell
node --test experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs
# 99 项：98 pass，1 skip，0 fail
npm.cmd test --prefix experiments/ai-rpg-engine/card-replica
# 26 pass，0 skip，0 fail
```

新增 15 项测试覆盖窗口边界、原文及 LSM 不变、初始资料两态、零插件装配、无卡字段硬编码、版本/hash/权限拒绝、撤权、严格配置、幂等、双标签并发、落盘失败/丢回执/重启恢复、旧会话拒绝 M1 与字节保留。
原插件生命周期、现有 Studio HTTP/预算/模型/分支与地球卡回归通过。唯一跳过是 `model-legacy-copy.test.mjs`，需要 `RPG_B3_LEGACY_COPY` 指向旧真实会话副本；未提供且未触碰历史数据。合成旧会话测试已通过，不能替代真实副本检查。
作者 prompts、原 assembly、原 model-runtime 与父市场页面四项基线 SHA-256 全部保持一致。

## 失败与修复历史
1. B1a 初次 19 pass / 13 fail：新制品经文本写入生成 CRLF，但 manifest 用 LF 字符串算 hash，导致制品隔离及依赖该目录的测试失败。改为稳定 LF 原字节写入并按实际字节登记；多插件测试的当前目录预期由2项更新为3项，原行为断言保留。同组复跑32/32通过。
2. 广泛回归初次 76 pass / 5 fail / 1 skip：独立工作区未安装 RPG 模块 ajv，5个HTTP测试文件不能启动。按原锁文件安装11个依赖，未改代码应付依赖缺失。
3. 补依赖后 95 pass / 1 fail / 1 skip：当前 HTTP 目录断言仍写2项，新增第三插件后失败。更新目录预期，保留两个旧插件所有生命周期/隔离断言。
4. 补充两项并发/输入冻结回归，最终98 pass / 1 skip / 0 fail。日志保留在忽略目录 `.rpg04-work/history-window-b1/`，不作为真实 Provider 证据。

## 分批文件与交付
- B1a：纯策略、manifest、catalog、host、策略/生命周期测试（5文件）；修复制品字节及现有目录断言后验证。
- B1b：history-state、history-runtime、history-assembly、history-session-store、存储测试（5文件）。
- 收尾回归修订：HTTP目录断言、状态约束、输入快照与新增并发测试（4文件）。
- 文档批：B0原状态备份、当前STATUS、本文、B1登记与插件操作说明（5文件）。
`DELIVERY.json` 保留 B0 历史登记；其中原 STATUS 字节现保存在 `B0-STATUS.json`，不是重写 B0 历史。当前候选使用 B1-DELIVERY.json。

## 回退与未授权事项
本批未提交、Push、PR、部署或调用真实模型，新增额度0、已用0。未新增面向用户的运行实例；旧预览、真实会话、账本不动。
B1 只在隔离测试目录创建新格式合成会话。正式接入前继续使用现有入口；代码回退仅撤回本批新增模块与四个插件相关增量文件。含第三插件登记的数据不能直接交给旧宿主；后续必须在副本验证。保留测试证据和配置。
下一批 B2 等待用户确认：接入严格同源 HTTP、实际发送记录、假 Provider 捕获及分支/模型/撤权联动，不产生付费调用。
