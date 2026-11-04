# 创作模块验收记录

## 人工与真实模型

- Earth OL 和 God 均已通过创作界面从空白作品重建，完成本地发布、作品包导出、在新的作品目录导入及桌面、390px 回放。用户已验收 B7。God 的 18 份资源及别名通过逐项核对。含实际作品内容的包、提示词、服务身份、会话和请求回执保存在操作者本机。
- Earth 的 B8-A 三次受控剧情调用已获用户通过。God 最近一次 B8-B 共三次剧情、三次记忆调用，使用 JP 路由的 Gemini。六份供应商回执均已收束。剧情设置延续与早期钥匙约定的实际召回已核对。
- **God 的 B8-B 仍为部分通过。** 第三轮模型给出的记忆基础版本为 1，宿主提供的是版本 2，因此拒绝整批更新。剧情保留，记忆处理到第 2 回合，五条旧记录保留。第三轮另有笔记执笔人归属漂移。改后的请求格式、旧记录回放和原子拒绝经离线复测，新格式的真实生成质量待单独授权后验收。
- C 组 Earth＋M2、D 组联合插件与分支，以及 E 组独立样例的真实模型试验均未派发。特殊 CG 收藏、变量状态引擎、并行模型对照、定时发布与跨平台转换继续单独立项。

## 可在独立工作树复现

1. 分别按 `experiments/ai-rpg-engine`、其 `ui`、`card-replica` 和仓库 `client` 的 lockfile 执行 `npm ci`。在实验模块运行 `npm run test:authoring`。独立样例覆盖世界书两级概率、归一化、编辑器、受限脚本与样式、CG、建议、插件组合、恢复、分支及受控宿主启动。
2. 在同一模块用 Node 测试运行器执行 `studio`、`plugins`、`card-replica/tests` 和 `god-card/tests` 中的测试文件。旧真实会话副本的验证使用既有只读的 `RPG_B3_LEGACY_COPY`；Linux 内核租约测试需要 Linux。Earth 旧 v1 入口恢复封存字节，行间、古代人生、超级修仙大世界和诡秘之主的入口在外层分流。
3. 从仓库根运行 `python -m pytest server/tests/test_rpg_authoring_execution.py server/tests/test_rpg_authoring_route.py server/tests/test_rpg_bridge.py server/tests/test_rpg_control_plane.py`。扩展回归包含控制面目录、工作负载、稳定聊天和调用来源测试。测试使用假供应商。身份、当前资格、路由、任务内容及额度均由服务端核对。
4. 在 `client` 运行 RPG 及 Studio 面板的 Vitest 用例，分别在 `client`、`experiments/ai-rpg-engine/ui`、`experiments/ai-rpg-engine/card-replica` 运行 `npm run build`。实际构建另行通过 HTTP 检查 Earth、God 和四条分流路径的页面、脚本与样式。

## 读取回归

对同一已终结的 1.7 MB 记录副本，重复内容验证与读取耗时 1.52–1.70 秒。文件重新读取并核对摘要后，命中已验证记录时为 85–89 毫秒，首次读取为 1.62 秒。该测量只覆盖存储与内容校验。

另用真实 HTTP 宿主和本地固定回复建立三回合会话，重复请求返回同一份 1,159,316 字节的响应。相同条件下逐次重新验证为 4.05–4.73 秒，复用已验证内容为 1.22–1.46 秒。两种模式下的响应摘要、回合、额度与调用次数一致。该对照没有使用真实供应商。
