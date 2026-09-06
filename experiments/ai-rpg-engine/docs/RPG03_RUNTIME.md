# RPG-03 运行核心

03F 本批说明；门禁统计以 `RPG03_ACCEPTANCE.md` 与 `RPG03_STATUS.json` 为准。代码通过显式端口运行，不组装主持提示词，不检索、压缩、总结或解释卡片专属玩法。

## 构造和操作

`/runtime` 导出 `createRuntime({store,modelAdapter,hash,pluginHost=null})`，返回 `{valid,diagnostics,value:runtime}`。`store` 提供 03D 的 read/write CAS；`modelAdapter` 提供 03E 的 generate 和冻结 evidenceKind；hash 为同步 SHA-256 函数。真实与模拟类别在实例创建时绑定，不能从卡片或模型输出切换。默认插件注册为空，必需插件缺失会阻止启动；可明确注入 03G 的 `createPluginHost({hash})` 受信宿主。

| 操作 | 输入和结果 |
| --- | --- |
| createSession | `{sessionId,cardPackage,playerSetup,pluginAuthorizations?}`；返回新会话 revision 0，声明状态取卡包初值。显式初始授权仍需受信宿主核对及独立 enable。拒绝覆盖已有会话。 |
| readSession | 同一资源绑定对象，不接受初始授权字段；只读验证会话，不检查插件就绪、不重放模型。faulted 时可读取磁盘快照及警告，但不会清除故障或替换内存运行状态。 |
| resumeSession | 同一绑定对象；重新读取检查点、验证 hash 并写入恢复后的 revision。自身有在途请求时拒绝恢复。 |
| generateTurn | 03C 生成请求及可选 `{onEvent}`；返回 `{session,generation}`。成功只代表形成 pending；失败也可返回保存的生成记录。 |
| cancelGeneration | 03C 取消请求；返回 `{session,generation,cancellation,outcome}`。有效取消先保存请求 revision，再中止传输，生成收敛后再次推进 revision。 |
| commitTurn | 03C turn-commit；核对当前 revision 和 pending generation/exchange，原子保存正式回合与明确选中的 state 字段。返回会话。 |
| discardTurn | 03C discard 请求；同样核对 revision/pending 身份，只放弃候选并保留原完成回执。返回会话。 |
| setPluginAuthorization | `{sessionId,expectedRevision,authorization}`；核对绑定及下一 revision，CAS 保存后停用对应插件，授权不会自动启用。该会话 active 期间拒绝。 |

公开诊断仅使用固定代码、severity、phase 和空 JSON Pointer，不转发端口异常、正文或堆栈。返回的 session、generation、draft event 本身属于私有剧情数据，调用方必须按 03D 私有目录边界保存；不能把完整返回值当作公开验收回执。

## 生成与状态边界

每次入口先校验并同步快照调用方数据；异步等待期间的调用方修改不会改变已经接纳的输入。相同 `(sessionId,generationId)` 先比较 inputSha256：相同返回现有 active/pending/终态记录，异 hash 冲突，不重新派发。输入 hash 包含资源、exchange、玩家输入、准备好的 messages、精确 model 和 settings，排除自然变化的 expectedRevision。

短串行临界区只处理 admission、CAS 和内存状态；模型等待不占用临界区，取消与读取可响应。同一实例最多一个模型请求；同一会话遗留 active 或未处理 pending 会阻止新的生成。启动前把 active 原子保存，完整文本必须解析为五键 proposal 并通过旧回合合同，才能形成 pending。格式错误、未知/只读状态字段、查询中的状态提案和非法信息模块引用均不能形成候选。

生成不会执行 suggestedActions，也不自动选择任何状态提案。commit 仅接受 pending 提案中明确选择的字段，query 不得选择状态变化。正式 turn、state、pending、generation 和 revision 在同一个检查点 CAS 中保存。原完成 receipt/finishedRevision 保持不变，显式处理候选时新增 resolvedRevision。

模型请求抛错或格式失败会生成失败记录；不补造模型、费用或上游身份。存储写入失败或确认不一致会将会话标为 faulted，并拒绝继续修改；需要显式恢复重新核对磁盘，不能从旧内存状态猜测写入结果。

## 取消、恢复和事件

有效取消只表示客户端已请求中止；当时的返回值不提前声称客户端传输或上游已经停止。适配器结束后，其已观察到的事实进入运行回执。已接受取消后，即使受信测试适配器忽略信号并迟到返回成功，也只保留取消结果和草稿，不形成 pending。完成后才发起取消时返回 `completed_before_cancel`，作为无状态变化的竞态结果；不修改已经完成的 receipt。失效 revision 仍会被拒绝。

恢复与再次读取不同：遗留 active 变为 interrupted，模型不重放；已有 pending 保留原候选与完成回执，仅推进会话 revision。最后一次完整提交必须可恢复；不承诺进程崩溃前最后一段草稿落盘。

onEvent 按 seq 发出绑定资源与生成身份的 status/draft/receipt。单个 draft event 最多 65536 字符，累计草稿最多 1 MiB 字符，并限制片段数；超限明确失败。观察回调收到独立快照；同步异常或异步拒绝不会提交状态或把已验证生成改写成失败，成功结果可带观察者 warning。核心不等待观察者 Promise，因此它不构成生成完成门禁或可靠消息总线。提交/放弃的最新状态由相应操作返回；本轮不建设跨进程事件系统。

## 消费边界

03H CLI 和验收 harness 负责提供文件、HTTP 及固定测试上下文。RPG-04 将来提供准备好的 messages；本轮 admission 中的空提案只用于复用旧合同检查命令引用，绝不发送给模型。RPG-05 将来消费事件、pending 和显式提交/放弃操作。可选插件返回值也不能自行改写正式回合；后续 UI、检索、长期记忆和市场没有由本轮接口推断为已实现。
