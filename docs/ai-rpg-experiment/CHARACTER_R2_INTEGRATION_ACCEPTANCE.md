# R2 最新主线组合与提交前验收

日期：2026-11-14。独立工作树 `C:/tmp/mm-r2-int`，分支 `codex/rpg-character-r2-integration`；本轮 fetch 的 main 固定为 `92849f3142ae67805e9cb627495ac8fab7d25df2`。本报告只描述这个基线，不承诺之后主线不再变化。

## 组合范围与结论

从原 `codex/rpg-character-r2-fix` 的 62 文件白名单带入：6 个已跟踪文件仅应用相对 `0cb019df` 的差异，56 个新增文件逐字节复制。仅 `authoring/server.mjs` 与新主线存在重叠，补丁无冲突应用且保留主线代码；其 R2 增量仍为两行进程内 `works` 接线。原工作树没有写回。

本轮新增帮助登记、更新单角色指南、六张中性 PNG 和两份验收说明；生产代码、运行指纹与兼容清单未再调整。群运行指纹仍为 `0f27356f7c87dc3ae1b60d8c101687d096e2fc6ea00a090e5ee714debce1d301`。没有通配旧哈希、修改绑定或旧数据迁移。

系统链路、新帮助与构建有可核对结果；整体测试不全绿。三个旧世界档案拒绝在干净主线同样复现；帮助全局失败也与主线相同。不能把“没有新增失败”写成“所有兼容已通过”。

## 实际检查

| 检查 | 结果 | 证据与边界 |
| --- | --- | --- |
| 角色全部测试 + R1 + R2 | 失败 | 102 项：99 通过、3 失败；全部失败为 a6563a9b 旧世界 Offline/Summary/Joint 档案，详见对照 |
| 干净 main 的 a6563a9b 对照 | 失败 | 同三项、相同错误；没有本候选文件时即拒绝 |
| 3fffd5b4 世界档案 | 通过 | 上述套件中的 Offline/Summary/Joint 恢复、续聊和篡改拒绝 |
| af3beb63、0cb019df 角色档案 | 通过 | 恢复、续聊、只读投影、隔离、漂移拒绝及原件哈希 |
| 最新 main 生成的世界档案 | 通过 | 从干净 92849f31 生成，组合副本恢复续聊，3/3 |
| 原 R1/R2 源码生成的数据 | 通过 | 在新目录生成中性记录后复制到组合代码恢复；R1 可追加消息、R2 完整记录不变；两个原件目录各 5 文件哈希不变 |
| 相关共享宿主 | 失败 | 整批 232 项：231 通过、1 失败。并发峰值断言 1 不等于 2；下述独立复验通过，不擦除整批失败 |
| 上述并发用例单独复验 | 通过 | 干净主线 1/1，组合副本 1/1，无源码或断言更改；记录为并行负载下时序敏感，不能保证稳定重现峰值 |
| Windows mock 网关 | 通过 | 196/196；独立短 basetemp，无供应商请求 |
| Linux 禁网 mock 网关 | 通过 | 同 196/196；本机已有镜像，network none、源码只读、临时 tmpfs，无端口或共享卷 |
| 相关前端及帮助 | 失败 | 120 项：117 通过、3 失败；与登记前干净帮助内容的 118 项中 3 个失败一致 |
| 六张新增 PNG | 通过 | 宽 780/960px，均小于 250KiB，替代文本、引用及基线一致，真实中性页面截图 |
| 全库帮助资产 | 失败 | 主线 24 条诊断与候选逐条相同；本次新增资产零诊断 |
| 父前端与聊天前端构建 | 通过 | 均执行 npm.cmd run build；父前端在最后正文修复后重跑通过，保留大包警告 |
| 最终教程重放与帮助页面 | 通过 | 两篇七步指南；最终正文后用两个全新浏览器上下文重放；帮助构建实际渲染，两页各一个 h1、各三图载入 |
| Git 差异、清单与保全 | 通过 | diff check、白名单与暂存空检查；原 62 候选及三份保全清单逐项核对 |

## 执行方法

在独立工作树运行以下命令。Node 存档测试使用真实目录；不能把 `.rpg04-work` 联接到外部路径绕过存储保护。`CHARACTER_R1_TEST_ROOT`、`CHARACTER_R2_TEST_ROOT`、TEMP/TMP 指向本轮独立短目录。

```powershell
node --test --test-concurrency=3 experiments/ai-rpg-engine/character/*.test.mjs experiments/ai-rpg-engine/character/conversation/*.test.mjs experiments/ai-rpg-engine/character/group/*.test.mjs
node --test --test-concurrency=3 experiments/ai-rpg-engine/authoring/server.test.mjs experiments/ai-rpg-engine/authoring/library.test.mjs experiments/ai-rpg-engine/authoring/store.test.mjs experiments/ai-rpg-engine/authoring/history-control.test.mjs experiments/ai-rpg-engine/authoring/summary-state.test.mjs experiments/ai-rpg-engine/authoring/summary-settings.test.mjs experiments/ai-rpg-engine/authoring/summary-jobs.test.mjs experiments/ai-rpg-engine/authoring/memory-state.test.mjs experiments/ai-rpg-engine/authoring/memory-jobs.test.mjs experiments/ai-rpg-engine/authoring/comparison-host.test.mjs experiments/ai-rpg-engine/authoring/published-chat.test.mjs experiments/ai-rpg-engine/authoring/published-player.test.mjs
```

网关选择 `test_rpg_authoring_execution`、`test_rpg_character_contract`、`test_rpg_group_contract`、`test_rpg_parameters_v2`、`test_rpg_authoring_route`、`test_rpg_control_plane`、`test_rpg_isolated_route`、`test_rpg_message_progress` 八文件；使用 `python -m pytest … -q -p no:cacheprovider --basetemp=<独立目录>`。Linux 使用同一清单，不把此前 256 项历史检查写成本次结果。

前端在 client 中执行 Vitest 的 `helpContent.test.ts`、`RpgLibrary.test.tsx`、`RpgAuthoringEntry.test.tsx`、`HelpCenterPage.test.tsx`（configLoader runner、maxWorkers=1、fileParallelism=false），以及 `node scripts/verify-help-images.mjs`。构建目录为 client 和 experiments/ai-rpg-engine/card-replica。

## 保留的失败与归因

- a6563a9b：Offline 为 `AUTHORING_RUNTIME_BINDING_MISMATCH`，Summary 为 `OFFLINE_SESSION_CORRUPT`，Joint 为 `JOINT_SESSION_CORRUPT`。干净主线与组合副本相同，未扩大本轮修复范围；后续若要恢复这一跨度，应独立审查精确运行合同，不能直接更新哈希。
- 帮助既有两项“11 步超过 8 步”“截图基线不一致”继续保留。最新主线另有语音指南预期 fefc14b0、实际 ae67eb25 的失败；截图断言当前首先命中 voice-library 的 c187d01e/ae67eb25 差异。历史 10 条资产诊断不改写，当前实际为 24 条。
- 本轮环境配置曾把测试目录做成目录联接，存储以 `AUTHORING_PATH_UNSAFE` 拒绝；改真实目录后才得到表中实际兼容结果。没有放宽安全检查。
- 新帮助原稿自带 h1，与阅读器标题重复，首次出现两个新增断言失败；只删除正文重复标题后，两项均通过。原日志保留。
- 主线视觉对照启动器首次错误使用仅 R2 新增的进程内 works 返回值；改用主线已有 HTTP 作品接口后完成对照，无主线源码改动。
- 共享宿主峰值断言的整批失败、两个独立通过均保留，不改测试、不表述成整批 232/232。

## 页面、材料与授权

预览仅为 loopback：角色库 `http://127.0.0.1:43140/characters`，群聊 `http://127.0.0.1:43141/groups`，帮助 `http://127.0.0.1:43142/help/use-character-group-chat`。使用原创旅人、观察员、茶亭主人、灯塔向导；固定回复明确标识离线。无正式角色文本、画像、真实会话、令牌进入公开资产。

本轮角色/群聊浏览器演练只触及 fixture provider，产品模型真实调用为零。两遍最终演练各产生一份独立单聊、群聊和中性导出文件，全部只在本地。群场景和版本固定、发送/接话区分、刷新四消息及草稿恢复均核对；移动视口与关闭面板焦点已检查。真实手机、Safari、用户人工体验、真实模型表现、全仓 CI 未验证。历史行动状态与事实回忆偏差没有关闭。

本地原始证据在 `C:/tmp/mm-r2-int-data`，存档夹具还使用独立工作树中被忽略的 `.rpg04-work` 真实目录。原候选 62 文件、原副本/材料 62 与 42 项、既有证据 75 项哈希不变；这些是不同清单，不能相加为去重总量。

没有提交、暂存、推送、部署、认证或调用模型。停止本轮自有预览即可停用；原工作树和原数据无须恢复。提交前若主线再前进，应按新差异重新评估组合范围；主线既有失败需要如实披露，不自行豁免合并门禁。
