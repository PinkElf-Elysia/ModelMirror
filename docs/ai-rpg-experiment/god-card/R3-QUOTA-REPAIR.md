# R3 双层额度与派发前拒绝修复

2026-10-11。用户明确授权补齐控制面、将其上限调整至约 100 次，并要求本卡账本保持 3/5。所有模型请求仍由用户手动派发。

## 已证实根因

手动会话 `88900dd0-e723-41cd-ad6f-284b4197660f` 的操作 `7c578511-e23c-485a-b657-7230f05665bb` 在约一秒内收到 HTTP 409，无原文、SSE 或标准回执。控制面运行记录 `chatrun_3a746a65c3c644b49a446ab96482dfcf` 为 `preflight_failure / rpg_fallback_limit_exhausted`，该运行的 Provider attempt 数为 0。请求 claim 的 hash 与本地 slot-5 一致。

控制面的上限按租户累计计算 RPG OpenRouter 后备路由已派发或未收束的尝试，与 God 卡独立授权账本不是同一个计数。此前控制面已达 5/5，而本卡新增额度只更新了本地账本，这是上次准备遗漏。

## 修复

1. `server/model_router/rpg_bridge.py` 对“完成预检但没有 dispatch”的情况返回绑定请求、模型、参数和 hash 的失败回执，明确 `dispatched=false`。只投影额度不足或路由不可用，不透传任意内部错误。
2. `studio/controlled-provider.mjs` 严格核对回执后将这类结果识别为明确失败；缺失或不匹配的回执继续保持未知，不以普通 HTTP 409 推断未派发。
3. 共享聊天提示区分控制面额度用尽、派发前拒绝与模型内容过滤。记忆任务测试验证剧情失败后不派发记忆，释放未派发预留，不自动退还已记录额度或重试。
4. 用户明确豁免 slot-5，新增 `R3-QUOTA-AMENDMENT.json`，绑定原请求与原 result.json hash。原 slot、claim、结果文件、历史和运行版本均保留；原会话仅离线收束为 `failed / CONTROL_BUDGET_EXHAUSTED`，没有伪造缺失的 Provider 回执，也没有迁移版本。

## 实际部署和状态

只替换共享 `modelmirror-server`：沿用原镜像及内存中的原配置，将 `RPG_OPENROUTER_FALLBACK_LIMIT` 从 5 改为 100，增加经过核对的桥接源码只读挂载。原环境中其他变量、全部原数据挂载和网络逐项验证保留，凭据没有输出或写入交付文件。原容器停止并保留为 `modelmirror-server-rpg-quota-backup-20261011`。其他容器不重建。

原 OpenRouter 域名公网覆盖通过 ExtraHosts 保留，出口防护未放宽。服务健康为 ok，Gemini 可用且 selectionRevision 不变。控制面累计计数仍为 5，上限 100；本批新增 Provider 调用 0。

本卡实际 API：`limit=5, used=3, remaining=2, reserved=0, attempts=5, exempted=2`。100 是控制面累计上限，不是本卡新增 100 次授权。两个豁免分别对应之前的 DNS 控制面失败与本次额度预检失败。

新手动入口：<http://127.0.0.1:18512/rpg-app/god/?session=760cc57c-6a47-4bc1-85ba-f8ed4fce0868>。God 角色配置保持原样，剧情与记忆均为 Gemini，记忆宫殿已启用，发送最多 2 次。实际浏览器显示剩余 2 次、发送可用、没有待确认提示。Agent 没有点击发送。旧会话继续只读，旧 18514 展示入口保留。

## 验证与边界

- 控制面桥接 29 项通过：在现有镜像、断网临时容器中运行 Git 源码快照及本轮补丁，不挂载真实数据或配置。
- 适配器 22 项通过；记忆任务、额度豁免、God 宿主与任务 43 项通过。
- 前端 `RpgMemoryPalace.test.tsx`、`RpgGod.test.tsx` 共 20 项通过。
- 卡片 TypeScript 与生产构建通过，独立输出到 `r3-quota/dist`；既有 R2/terminal 产物保留。
- 保留测试环境失败历史：Windows 默认临时目录拒绝访问；指定临时目录后 28 通过、1 因缺 duckdb 失败；断网容器只读源码挂载又遇 World/MCP 初始化写入限制。最终改用纯 Git 快照在容器内可写测试目录运行同一组 29 项，全部通过。未禁用检查。
- 浏览器可见检查完成，截图仅在工具内查看；本地 PNG 归档因工具写权限失败，不宣称已归档。
- 未运行新的真实生成，不能据此宣告 God 记忆提取或召回通过。没有 Commit、Push、PR。

操作意图、部署前桥接源码、部署结果、原会话备份、行政结算证据、准备记录及 hash 清单在忽略目录 `experiments/ai-rpg-engine/.rpg04-work/god-card/r3-quota/`。当前桥接部署 hash 为 `f8019d65912ff596d42a2f8bd62b7ce19d6b553e017b7038fc5cee5c9507542d`。

## 回退与后续部署

先停止本轮自有预览，保留所有账本、豁免、原文及会话。共享回退须确认没有活跃任务，再停当前 server、保留其记录，把原备份容器恢复原名并接回部署意图中的原网络；两个 server 不同时运行。原容器仍共享原持久卷，禁止删除卷。回退到上限 5 将重新阻断该路由，不得擅自回写原会话为未知。

本次是当前容器的定点替换，不代表共享栈更新到了此 God 分支。当前容器重启保留设置；今后使用原 Compose 文件重新创建时，必须显式保留本批 100 次环境变量、桥接补丁及域名覆盖。部署补丁从本工作区 `r3-quota/bridge-runtime.py` 只读挂载，迁移前不得删除该文件。正式后续集成须从届时主线审查并应用窄补丁，不用本分支旧版服务文件覆盖共享新版本。
