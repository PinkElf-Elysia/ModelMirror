# 消息工具输出编辑增补

基线：origin/main d9f20fe6；分支 codex/rpg-output-edit；工作树 C:/tmp/mm-rpg-output-edit。
目标：人工修改已保存模型输出，不重新生成正文；显示、复制、导出、后续上下文与派生记忆保持可解释的一致性。
范围：既有消息工具的 earth/god 会话、auto-summary-v2 的既有编辑动作、相关测试与帮助。无新依赖、无数据库迁移，不修改其他工作树或共享服务。

## 实现边界

- 输出编辑是会话内追加的修订；provider 原文、history 和生成回执不改写。新请求按修订快照构建，旧请求仍能重建验证。
- 新能力 session.message.edit 需要消息工具 1.1 的明确会话授权。保存有版本检查、会话锁和同 ID 幂等恢复；生成或未确认插件任务期间拒绝。
- 新分支只携带截至分支点的输出修订；编辑父路线不回写既有子路线。
- 旧滚动总结和记忆不暗中调用模型重算；已覆盖回合的修订作为明确的数据纠错附入后续上下文。未来总结和记忆的新来源使用修订内容；已有条目、插图和后续剧情保留。
- 自动总结 2.0 沿用单次小总结更新；先保存正文并清空过时小总结，再请求小总结。失败或未知结果保留正文且阻止自动重放。大总结不自动重建。
- 兼容映射仅允许本基线中列明的旧宿主文件指纹，再经过原完整校验；不修改存档的原描述符和回执，不放宽内容/配置/任意版本漂移。
- 同一输出读取规则穿过显示、导出、四代历史装配和两个卡片适配器；因此触及多个小适配点，无法只改前端而安全实现。各批保持单一目标，测试覆盖跨层一致性。

## 验证

本地预览端口 18542，独立 preview-data，合成角色林遥，无真实模型凭据或调用。
可见流程：打开已有回复 → 编辑回复 → 修改并取消（原文不变）→ 再编辑保存（标记已编辑）→ 刷新（修订保留）。
自动化：studio/output-edits.test.mjs、auto-summary-v2.test.mjs、message-tools.test.mjs、transcript.test.mjs、summary-integration.test.mjs、memory-integration.test.mjs；前端 RpgOutputEditor.test.tsx。
构建：card-replica npm run build；client npm run build。
执行结果：
- 相关宿主回归 48 项通过；此后增加的桥接意图互斥检查，最终 output-edits.test.mjs 8 项通过。
- 前端编辑器、会话状态、滚动总结、记忆宫殿及帮助目录共 51 项分批通过。
- card-replica 和 client 构建通过；保留既有动态导入/大包体积告警。
- git diff --check 通过；本次差异常见凭据模式扫描无匹配。
- 本地可见界面验证了取消、保存、已编辑标记和刷新恢复。浏览器工具两次保存截图返回 EPERM，截图尚未归档。随后使用干净端口 18543、独立数据和浏览器 origin，按已写教程完成编辑、保存、查看原文、刷新恢复重放。正式 PR 前仍须完成仓库 AGENTS.md 2.6 的截图归档门禁。
- 实测使用本地替身，真实 Provider 调用为 0；不证明真实模型总结质量。隔离预览已关闭，共享栈未变更。

## 回退

部署回退代码前保留完整会话目录与追加修订；不要覆写或清理用户存档。旧代码可能只显示 provider 原文，故包含 outputEdits 的存档应保留新版只读导出供核对。没有执行提交、推送、PR、合并、部署或真实调用。


## 主要文件

- card-replica/src/output-editor.tsx、chat.tsx：编辑弹窗、保存及已编辑标记。
- studio/message-tools.mjs、output-edits.mjs：授权写入、修订校验与统一读取。
- studio/history-assembly.mjs、summary-assembly.mjs、memory-assembly.mjs：新版上下文与原回执快照。
- studio/auto-summary-v2.mjs：小总结失败保留正文与后续修订资料。
- studio/transcript.mjs、god-card/lib/http.mjs、studio/server.mjs：导出和显示适配。
- studio/output-edit-compatibility.mjs：精确旧指纹兼容；output-edit-baseline.json 为测试基线。
- plugins/message-tools.mjs、message-tools.manifest.json、host.mjs、catalog.mjs：1.1 权限与能力。
- client/src/content/help-center/articles/edit-rpg-reply.md：使用教程与同步边界。

## 2026-11-05 真实调用授权与路由阻塞

用户授权两轮真实正文及关联插件调用；采用 Gemini 3.8 Flash、独立会话数据，覆盖历史窗口、滚动总结、记忆宫殿和自动总结 2.0。

首次宿主请求返回 HTTP 409 / CONTROL_RECEIPT_INVALID。只读核对桥接 claim 和后端 SQLite 账本后，确认 run chatrun_1468a47b443f443daaa15974e9105fc3 / attempt chatattempt_81421102c0ad4796bc168041bb4e36cb 为 dispatched=0，错误 rpg_selection_changed；目录选定 OpenRouter 连接，派发预检选中 newAPI 连接。真实模型调用 0，插件调用 0。原失败会话和 claim 保留，不重放。证据在忽略目录 .rpg04-work/output-edit/real-20261105/reconciliation.json。

用户随后授权独立修复、测试部署后继续实测，并明确另一工作树正在修复相同问题，提交不得重复。已核对修复归属为 C:/tmp/modelmirror-rpg-pr-final-20261105；本任务不包含该路由修复、不重复部署或提交。新建的 C:/tmp/mm-rpg-route-binding-fix 工作树保持无改动。待对方测试部署完成后继续原两轮授权。

## 2026-11-06 恢复后的真实验收

PR #443 已合并（6977d86c）；只读核对共享后端的 chat_stable.py、rpg_bridge.py 与合并源码一致，Gemini 3.8 Flash 目录可用。另一工作树负责的路由代码未复制、部署或重复提交。

结果为部分通过：2 次真实正文派发均收到上游 HTTP 200 和完整结束标记，但正文为空，桥接记为 rpg_incomplete_output。首份失败后用户明确补充一次正文调用并要求再次失败即停；补充调用同样空正文，已停止，无自动重试，未创建有效回合。不能宣称完成两轮编辑、历史窗口、滚动总结和记忆宫殿真实联动。

自动总结 2.0 单独实测通过：合成中性正文编辑后，真实 Gemini 小总结包含修订的钟楼、周岚、旧码头及青灯七号事实；originalRaw 和既有大总结不变，仅发生 1 次 small 调用，无正文重新生成。这是导入夹具上的真实辅助调用，不是完整剧情生成链路。

本轮计数：2 次正文尝试失败 + 1 次小总结成功 = 3 次 Provider 派发。记忆及旧滚动总结调用 0。前一日零派发失败另行保留。证据：.rpg04-work/output-edit/LIVE_ACCEPTANCE.md、live-receipt-index.json。完整模型原文及 SSE 保留本地忽略目录，不提交。隔离测试宿主已结束，共享服务未修改。

本功能源码仍基于 d9f20fe6；最终 PR 前还需整合 #443 的相关前端/运行时变化，解决截图归档门禁，并补完有效正文的真实联动验收。当前没有提交或发布授权，本轮未执行 Commit、Push 或 PR。


## 2026-11-06 low 隔离联动验收范围

用户授权：输出编辑工作树内启用 low，进行两轮真实正文及相关插件实测；不改共享默认参数，不影响其他工作树。用户明确沿用当前结构：主会话两轮＋真实原文/修订版本导入独立自动总结 2.0 验证。

本批只增加显式选择的隔离 fetch、固定 low 的后端 transport、两份离线测试及本文档。既有消息编辑实现不改。共享应用不导入这些测试入口；调用在原后端容器中的一次性工作进程和隔离 SQLite 副本执行，未修改共享代码、目录或资格配置。凭据不导出。

计划上限：story 2 次、memory 2 次、small 2 次，共 6 次。主会话只启用历史窗口和记忆宫殿，确保实际上下文采用历史窗口；旧版滚动总结不启动。2.0 导入真实首轮输出，先生成原小总结，再编辑并更新小总结；预置大总结若用于不变性检查会明确标注为夹具。正文不因编辑重生。所有调用均 low、max_tokens=16384，未知结果不重试。

验收：实际出站仅增加 reasoning.effort=low；第二轮 assistant 历史是修订文本，旧记忆通过修订说明与新剧情来源衔接；显示/导出与修订一致，原文/生成回执仍保持；2.0 小总结更新而既有大总结不变。风险仅为已授权调用，回退为关闭测试宿主；保留证据和 claim，不清理或重置原会话。

## 2026-11-06 low 六次真实调用结果

本批完成 2 次 story、2 次 memory、2 次 small，共 6 次真实派发，自动重试 0 次。两次正文分别为 12453、14386 字符，均收到 STOP 与完整结束标记；不再出现空正文。两次 memory 均解析并应用成功，两次 small 均保存成功。全部实际出站为 Gemini 3.8 Flash、reasoning.effort=low、max_tokens=16384、temperature=0.7、top_p=0.8，返回 provider 为 Google AI Studio。原始流报告的 reasoning_tokens 均为 0；这只是本批回执值，不代表模型永久关闭推理。

实际验证结果：

- 主会话第一轮生成后人工编辑；保存编辑不触发正文或辅助模型调用。界面数据和导出读取修订文本，原 provider 正文、原请求和生成回执保持不变。
- 第二轮实际使用 history-window，选入第 1 轮的修订 assistant 文本及明确修订说明。第二轮用户输入未重述答案；模型延续了钟楼、周岚平安未离开、旧码头完好开放和口令“青灯七号”。只有两轮真实剧情，本批不证明多轮历史裁剪的真实模型表现。
- 记忆宫殿处理至第 2 轮；人物条目从版本 1 更新到版本 2，并含修订口令和汇合事实。编辑保存时不暗中重算旧记忆，后续更新来源为第 2 轮实际剧情。
- 自动总结 2.0 按用户选择，在独立会话导入真实第一轮及编辑版本；两次真实 small 调用完成替换，originalRaw 保持。既有大总结使用明确标记的合成不变性夹具，编辑前后相同；本批没有调用大总结模型，也不证明主会话自动触发 2.0。

内容质量限制：

- 记忆更新只修改了人物条目，仍保留旧“河港镇西码头与红星桥告示”中的渡船停运内容，与新剧情渡船正常运转冲突。已证实第二轮遵循人工修订，但不能据此宣布记忆全库消除矛盾；本批未追加清理调用或自动删除旧条目。
- 编辑后小总结正确记录地点、码头和口令，但补写了原修订文本未明确给出的“林遥放下悬着的心”。因此同步链路通过，不等于逐字事实质量完全通过。

证据与隔离：

- 本地忽略目录：experiments/ai-rpg-engine/.rpg04-work/output-edit/low-integration-20261106/。verified-result.json 汇总 6 次回执；final-transcript.txt 为最终可见导出；first-reply-original.txt / first-reply-edited.txt / second-reply.txt 供人工验收；small-before.txt / small-after.txt 和 memory-comparison.json 保留插件对照。
- backend-evidence 仅归档本批请求、流、回执和不可重放 claim，不包含数据库或密钥。6 份有效请求逐一验证哈希，与桥接基准相比唯一新增字段为 reasoning.effort=low。
- isolation-checks.json 确认共享 rpg_bridge.py 哈希保持不变，本批 6 个 run 在共享账本中为 0 条，均在隔离副本；未复制 credential-master.key。旧 medium 失败与 B 组证据不覆盖、不重放。
- 离线验证：python -B -m pytest server/tests/test_rpg_output_edit_low.py -q --tb=short -p no:cacheprovider：11 passed；node --test --test-reporter=spec（isolated-low-fetch.test.mjs、output-edits.test.mjs、auto-summary-v2.test.mjs）：14 passed。live.mjs verify 返回 FINAL_CHECKS_PASS，analyze.py 与 check-isolation.py 完成。

本轮只新增 opt-in 隔离 transport/fetch 与测试，并更新本记录；既有输出编辑实现保持。共享默认配置和其他工作树未改，无 Commit、Push、PR、Merge 或共享部署。回退方式为不启用隔离 fetch/transport；实测宿主已结束，保留会话、回执及 claim 以防重放。原基线整合及截图归档仍是后续 PR 门禁，不因本批模型调用完成而免除。


## 2026-11-13 PR 收尾授权与未完成门禁

用户明确要求“无需继续验证，可以收尾后提交PR，但需注明测试时相关限制和表现”。本次据此授权提交、推送与创建 PR；不授权合并、部署或追加模型调用。按用户要求不重跑测试、不追加最新基线可见预览/教程重放/截图归档。这是对本次补验的明确免除，不记为门禁通过；保留 11 月 5–6 日已执行结果和限制。

发布核对时 origin/main 为 7b1f840c，而功能及模型实测基线仍为 d9f20fe6。主线其后已接入统一聊天自动总结 2.0、流式排版及当前模型重新生成；与本分支有 14 个文件重叠。为保留用户批准的“旧结构主会话＋独立导入 2.0”验收范围，本次不擅自扩大为新主线集成实现；PR 明示该兼容性尚未验证。若存在合并冲突，保留为 Draft 待集成，不以旧测试结果宣称可直接合并。

提交仅含消息工具输出编辑、相关适配与测试、显式 opt-in 的 low 测试适配器、帮助文章和本记录。运行会话、模型正文、SSE、账本、诊断脚本、密钥、构建产物和日志保留在忽略目录，不进入 PR；其他工作树已合并的路由修复不重复提交。停用隔离入口即可停止 low 覆盖；功能回退前须保存带 outputEdits 的会话及修订版导出，旧代码可能只展示 provider 原文。


发布合并检查已确认：git merge-tree --write-tree --name-only HEAD origin/main 报告 10 个文件冲突，未修改工作树。涉及 helpContent.test.ts、help-center/index.ts、AutoSummarySession.tsx（主线已删除）、card-replica/src/chat.tsx、session-client.ts，以及 studio 下 auto-summary-v2.test.mjs、history-assembly.mjs、memory-assembly.mjs、server.mjs、summary-assembly.mjs。PR 以 Draft 提交：功能与证据可审阅，但不可直接合并；整合统一聊天和处理旧运行时指纹仍待完成。本次不将冲突解决或最新主线兼容性写成通过。


## 2026-11-13 主线冲突修复（取代上节待整合状态）

用户随后明确要求使用命令行解决列出的 10 个冲突。已合入 origin/main@7b1f840c，保留主线统一聊天、流式渲染、当前模型重新生成及历史版本/回收站；AutoSummarySession.tsx 按主线删除。帮助条目与测试保留双方内容，没有恢复旧入口或重复实现共享路由修复。

必要的语义合并：空白开局的历史选择使用修订文本；旧请求重建同时保留修订次数与 2.0 快照；统一 2.0 的显式小总结重算读取修订正文，旧总结续聊带修订说明；重新生成截断被移除回合的修订，历史版本恢复带回对应修订。编辑按钮仅显示在本次实现的 earth/god 宿主。精确兼容映射增加主线源字节及五个主线运行时描述符，完整内容/设置/指纹校验保留，不通配放开版本。

当前统一聊天保存编辑仍不调用模型，不自动重算小总结；用户可在已有总结界面明确重算。旧独立 2.0 导入实验的自动小总结行为仅作为历史测试证据，不冒充新入口行为。先前记忆残留冲突和情绪补写限制仍然有效。本次冲突修复未派发任何真实模型调用、未改共享默认配置、未部署。

本地检查（仅为本次代码合并；不重做真实模型验收）：

- 通过：node --test --test-reporter=spec experiments/ai-rpg-engine/studio/output-edits.test.mjs experiments/ai-rpg-engine/studio/auto-summary-v2.test.mjs experiments/ai-rpg-engine/studio/default-summary-v2.test.mjs experiments/ai-rpg-engine/studio/session-lifecycle.test.mjs。29 passed，0 failed；包含统一 2.0、版本恢复和五个主线精确描述符/篡改拒绝检查。
- 通过：card-replica 目录 npm.cmd run typecheck。
- 失败：client 目录 npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/content/help-center/helpContent.test.ts src/pages/RpgOutputEditor.test.tsx。19 passed、1 failed。输出编辑器通过；剩余帮助截图基线检查失败，main 中 manage-rpg-history 元数据为 479cd636，但引用 /help-center/fbe65f12/regen.png。已直接核对 origin/main 的文章和目录包含同一不一致，未改断言或补造截图。
- 失败：client 目录 npm.cmd run typecheck，TS7016：stream-output.tsx 导入 stream-dom.mjs 缺少声明。该模块及 tsconfig 与 origin/main 相同，主线没有对应声明文件；未扩展本次范围修复。未声称在另一完整主线环境重新运行过此命令。
- 未运行：全量回归、生产构建、浏览器重放、截图补验、真实模型复测。早先基线实测结论不扩大为本次主线验收。

初次本地检查发现运行时映射和帮助排序问题，均已修复并重跑对应检查；上述结果为修复后的最终状态。PR 保留 Draft 和已知检查限制，冲突状态以推送后的 GitHub 查询为准。
