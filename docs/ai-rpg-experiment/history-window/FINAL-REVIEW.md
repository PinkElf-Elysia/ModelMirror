# M1 历史窗口：已验收本地候选收尾

结论：B0–B3 分批验收及 B4 三份真实输出均经用户确认；真实第三轮已裁去第一回合，仅携带第二回合。当前为已验收本地候选，不是发布、共享部署或长局记忆质量认证。用户最后指令为“可开始收尾”。

## 范围与版本

工作区 C:/tmp/modelmirror-rpg-history-window；分支 codex/rpg-history-window；基线 61b6eea80152904eb8fb3cc0ebba5e7647594828（PR #383 已合并的基线）。主工作区未动。当前实现仍未提交，HEAD不包含本轮增量。
运行hash保持 0e76f0d2997a66e8fbedb57f97a7a2727bfd2344c08154f978897fafbb8e62c9；收尾仅更新验收/状态/帮助/交付文档，没有改动已验收宿主、插件、装配、作者文本、角色模板、世界书策略或模型参数。Studio布局、旧RPG05不变。

实现文件分组：plugins/history-window.mjs 与 manifest、catalog/host；studio/history-* 的配置存储、装配、版本、分支及派发边界，以及server接线；card-replica/src的会话设置浮层/客户端恢复与chat接线；父client仅RPG市场、帮助和相应测试。tooling/history-window-b4-* 为有明确动作的本轮测试入口，无启动即生成或自动重试。详细文件与hash见FINAL-DELIVERY.json。

## 最终有效状态

- 稳定ID rpg.history-window，默认未安装/未启用，新会话配置默认3回合、开局快照关闭。
- 真实验收会话“许澄”当前显式启用，窗口1、补入开局角色资料关闭，配置revision=1，没有未确认设置。
- 真实第三轮请求4条消息：冻结system → 第二轮原始user → 第二轮完整assistant（含卡内记忆区）→ 当前输入及前后置词。首轮与开局原始角色快照未补入。完整持久历史仍为3回合6条消息。
- 用户逐份通过；额度4/4已用完：必要认证1，RPG生成3，剩余0。页面发送禁用。不会再派发。
- Gemini模型及0.7/0.8/16384参数保持不变；无降级、切供应商、摘要、正文修复或重试。

## 证据与验证结果

| 检查 | 结果 | 证据 |
|---|---|---|
| 插件/宿主 Node 测试 | 通过106、未运行1（显式skip） | B3-REVIEW、studio-plugins.log |
| 地球卡 tests、TypeScript、生产构建 | 通过26及构建 | B3 earth-verify / earth-studio-final.log |
| 受影响RPG前端、Studio与帮助 | 合计45通过（分次结果） | B3日志及15项帮助修复重跑 |
| 收尾帮助测试 | 15通过 | experiments/ai-rpg-engine/.rpg04-work/history-window-closeout/help-tests.log |
| 收尾父前端生产构建 | 通过；既有大chunk警告保留 | experiments/ai-rpg-engine/.rpg04-work/history-window-closeout/client-build.log |
| 当前运行hash、窗口状态、完整历史、预算 | 通过 | experiments/ai-rpg-engine/.rpg04-work/history-window-closeout/live-state.json |
| 三轮实际请求/原文/浏览器原文hash | 通过，第三轮实际越界 | B4-FIRST/SECOND/THIRD-EVIDENCE.json |
| 人工验收 | B0–B3、三份B4输出通过 | ACCEPTANCE.json、B4-AUTHORIZATION.json |
| 全局帮助图片检查 | 失败：3项原基线未引用图片，0新增 | B3当前/隔离基线同样失败日志 |
| 真实旧会话副本降级兼容 | 未运行 | 未提供RPG_B3_LEGACY_COPY；合成测试不替代 |
| Commit/Push/PR/共享部署 | 未运行、未授权 | Git未暂存；仅本地候选 |

收尾测试命令：client目录 npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/content/help-center/helpContent.test.ts；npm.cmd run build。原地球卡/宿主代码及产物hash没有变化，不为收尾文档重复消耗验证或真实额度。git diff --check通过。扫描全部变更与非忽略新文件路径，未见.env/依赖目录/产物目录/本机数据混入；常见凭据字面量模式未命中。这是模式扫描，不宣称能证明不存在一切敏感数据。
帮助最终已在正式浏览器复核，补充“完整历史仍显示”与“实际发送窗口”的区别，并将真实输出状态从未运行更新为已人工通过。既有截图为B3离线实拍；没有伪称B4新增截图。

## 已知问题与不外推边界

1. 全局 /rpg-app/api/status 的 contextPolicy:retain-all 仍是静态卡片默认描述，无法表达逐会话窗口，容易误读。本轮收尾没有改变冻结运行版本；运维须读取会话 history-window.effectivePolicy、回合 historyPolicy 和真实request.json。此字段语义澄清列为后续发布前复核项，未擅自视为已修复。
2. 原基线帮助图片 b266b870/studio-beta-headings.jpg、eeb5bbd2/creator-cache.jpg、eeb5bbd2/studio-layout.jpg 未引用，未动其他任务材料。
3. 只覆盖一条短程真实轨迹和窗口1/快照关闭。不能证明长局记忆稳定性、分离正文与记忆区的因果贡献，或快照开启的真实模型效果。后续路线仍是M2滚动摘要→M3条目式记忆→M4分层/时态/视角；没有预建引擎。
4. 旧会话不升级、不追认版本；真实旧数据副本测试和完整键盘焦点循环未补作。既有B3边界保留。

## 证据保留、回退与下一步

收尾前72个已验收文件和58个产物逐hash核对并复制到 experiments/ai-rpg-engine/.rpg04-work/history-window-closeout/accepted-snapshot；B0/B1/B2/B3及三次真实输出的旧清单、失败日志、原文与回执保留。FINAL-DELIVERY记录当前文件、两处产物和本地证据hash，排除自身；不会通过改写旧报告制造历史通过。
18461真实验收入口、18455离线入口、18453原型均保留；账户/服务凭据未显示或写入仓库。原始调用数据与完整作者输入位于忽略目录，公开候选只包含必要报告与hash。
回退优先停用插件，保留原文、历史、节点、分支、设置和账本。旧宿主降级须另用数据副本验证，不能直接复用新插件登记数据。只允许停止经归属确认的本轮实例，不例行关闭用户验收入口。
本地收尾完成；新Commit/Push/PR及共享部署需各自明确授权。发布前仍须刷新上游、核对冲突与范围，不把本次快照当作未来发布时最新基线。
