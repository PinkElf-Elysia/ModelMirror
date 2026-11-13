# 哈利波特恢复与折叠显示修复

2026-11-13；在 `codex/rpg-harry-potter` 原有未提交复刻结果上继续，不覆盖其他工作。

范围：Windows 本地宿主进程互斥与已证明属于受管宿主的遗留占用恢复；哈利波特回复的纯显示投影。保留 host/transport 原始指纹、提示词、所有已保存原文、预算与回执。无迁移，无新增依赖，无真实请求，不读取凭据。

恢复以 Windows 命名管道作为进程生存期互斥，硬链接证明 owner.lock 的确由受管宿主创建；异常退出后保留旧标记再接管。活进程、未知/替换标记、软链接、抢占和不完整启动保持拒绝。Linux 容器继续使用现有 flock 宿主，不变更其协议。

显示仅针对哈利波特：复用现有安全解析器，修复显示副本的 HTML 结构并默认收起 details；保存的 raw、导出和模型历史不修改。流式片段与完成回复一致，用户展开状态应保留。

分批最多五个逻辑源码文件。验收：独立子进程强制终止及同目录重开、第二活宿主拒绝、未知占用不接管、旧四轮存档复制件字节一致、零模型 POST、原文哈希一致；显示合成反例和已有四轮输出离线回放；相关 Node/Vitest、卡片和主前端构建。

回退：撤销新宿主入口接线与纯显示投影，保留旧标记归档和存档。禁止通过删除存档、关闭校验或追加调用解决失败。模型补写与时间线偏差继续保留在原验收报告。

## 实现与验收结果

- `studio/local-owner.mjs`：Windows 本地目录互斥、同 inode 硬链接凭据和死亡进程核对。第二个活宿主、未知/替换/孤立标记、活 PID 与目录软链接均拒绝；仅归档已证明属于受管进程的遗留标记。
- `studio/server.mjs`：根目录及各卡宿主接线；插件显式关闭与服务关闭共用幂等释放流程；新增 `closed` Promise 供调用方等待资源释放。哈利波特 API 仅重建返回值中的显示 HTML。
- `card-replica/lib/harry-potter-presentation.mjs`、`stream-presentation.mjs`：复用安全解析器，平衡显示结构、默认收起 details；其他卡默认行为不变。未修改冻结的卡片 host、transport 或提示词。
- 测试：`studio/local-owner-child.mjs`、`studio/local-owner.test.mjs`、`card-replica/tests/harry-potter-presentation.test.mjs`、`client/src/pages/HarryPotterOutput.test.ts`。帮助页同步说明恢复范围与折叠操作。

离线复制件来自原四轮真实验收会话 `3c64013d-8d00-46fe-a617-41c0c849f953`。最终代码启动 PID 55592，强制终止后由 PID 66968 同目录打开：四轮 API 投影前后一致、runtime.compatible=true，原存档与复制件逐字节一致，36 个派发证据文件不变。预算仍为 used=4、remaining=0。四轮各有 9 个显示面板，全部默认收起；原始回复中的 open 与多余闭合标签保留在 raw 中。

存档 SHA-256：`df865ca1b157fec36bbb32fc3b1ae67686ba34712c6c66e72f531e5c5dc8d78e`。8 个冻结资源哈希全部匹配。提示词系统正文 SHA-256 仍为 `10859780a0de93d01a2be111d31abf1fc3487778d6df9ae7d20bfe992e861f55`。

没有读取凭据、没有新增真实调用。回放宿主只提供内存中的固定模型目录，拒绝所有模型 POST，不使用真实服务令牌。原始验收目录和共享服务未改动。

验证命令（除注明外在 `experiments/ai-rpg-engine` 执行）：

| 命令 | 结果 |
| --- | --- |
| `node --test card-replica/tests/harry-potter-presentation.test.mjs studio/local-owner.test.mjs harry-potter-card/harry.test.mjs` | 11/11 通过；后续串行扩展中这些用例仍全部通过 |
| `cd ../../client; npx.cmd vitest run src/pages/HarryPotterOutput.test.ts src/pages/HarryPotterCard.test.ts` | 4/4 通过，用户展开/收起状态在流式增量中保留 |
| `node .rpg04-work/hp-repair-20261113/verify-replay.mjs replay-final-data` | 四轮强杀重启、兼容性、正文/回执/预算/冻结资源通过 |
| `cd card-replica; npm.cmd run build` | 类型检查和构建通过，保留原有混合导入提示 |
| `cd ../../client; npm.cmd run build` | 类型检查和构建通过，保留大 chunk 提示 |
| `node --test --test-concurrency=1 studio/local-owner.test.mjs studio/archive-plugins.test.mjs studio/card-entry.test.mjs harry-potter-card/harry.test.mjs card-replica/tests/harry-potter-presentation.test.mjs` | 19/20；诡秘插图桩返回 unknown；单独复跑该用例通过。此前并发批次斗罗同一插图断言失败，单独复跑也通过。未定位该间歇问题，不宣称扩展套件全绿；未改插图实现或降低断言 |
| `git diff --check` | 通过 |

浏览器实测四轮记录、状态面板展开（23 加隆）/收起、刷新后仍为四轮并默认折叠，发送禁用且可用次数为 0。回放页：`http://127.0.0.1:18568/rpg/harry-potter/chat/3c64013d-8d00-46fe-a617-41c0c849f953`。仅用于读取旧真实输出，不是新生成验收。

本机证据：`.rpg04-work/hp-repair-20261113/replay-evidence.json`、`frozen.json`、`verify-replay.mjs`、`folded.jpg`。原模型补写与时间线偏差继续见 [原真实验收记录](HARRY_POTTER_REAL_ACCEPTANCE_2026-11-13.md)。

## 范围与剩余限制

自动恢复适用于新版成功打开过的 Windows 本地盘受管宿主。旧版本没有硬链接凭据的标记不会自动接管；启动时尚未建立凭据、恢复途中或正常关闭时移除硬链接后又被强杀的短窗口，也会保守要求人工核对，不删除未知占用。Linux 容器仍走原有 flock 路径，本轮未重新进行容器集成测试。

本次证明已保存回合的恢复与显示，不证明生成中被杀的未知请求能够完成；未知请求继续禁止自动重放。未追加模型质量试验，未改变时间线或模型补写内容。未提交、推送或部署；回退仅撤销上述入口接线和显示投影，保留所有存档与 owner-dead 归档。

后续插图排查：已补齐原子发布遇到短暂文件占用时的有界重试，确定性故障注入与 32 项相关回归通过，扩展插件套件连续三次共 24 项通过。历史失败保留，详见 [插图 unknown 修复记录](ILLUSTRATION_UNKNOWN_FIX_2026-11-13.md)。没有追加真实调用。


最终主线整合与验收以 [HARRY_POTTER_FINAL_ACCEPTANCE_2026-11-13.md](HARRY_POTTER_FINAL_ACCEPTANCE_2026-11-13.md) 为准；本文中的未提交、旧基线和测试数均为对应历史阶段记录。
