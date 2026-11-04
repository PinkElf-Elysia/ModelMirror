# 古代人生 · 命运卷轴

从模镜 RPG 列表进入 `/rpg/ancient`。依次选择人生道路、配置人物、生成命书，在最后一步复制，再手动粘贴到卡片外的输入框。网页的「装配开局（离线）」只返回装配结果的哈希。真实调用通过服务端 `createAncientHost` 显式配置，网页入口没有模型额度。

## 已验收范围

2026-11-04 用户通过原工作树的人工验收。四种人生道路均在原站和本地浏览器操作过，相同输入对应的命书文本一致。390px 手机视口验证了复制、粘贴和离线装配。作者提示词按 DOCX 正文段落原样提取，UTF-8 文本 SHA-256 为 `4a7891e458fdd2a418e1e9de2f1fac5a962b68cf995a251fa63f5cfbf01a001b`。

| 验收步骤 | 实际结果 |
| --- | --- |
| 中性开局，未挂插件 | Gemini 生成一轮剧情 |
| 指定开局，第一轮 | 剧情完成，记忆宫殿写入五条 |
| 同会话，第二轮 | 请求召回五条记忆，滚动总结覆盖第一轮，记忆库处理至第二轮，共六条 |
| 历史窗口 | 配置为一轮。滚动总结启用时接管裁剪；本次没有独立验证长会话截断 |

三个剧情回执、两个记忆回执和一个总结回执的实际模型均为 `google/gemini-3.8-flash`，均无重试。精确路由另有一次成功的认证调用。调用发生在已验收工作树，PR 收尾只运行离线验证。剧情回顾把「不足五千户」写成「五千户」，保留为本次观察到的数量偏差。

## 验证和回退

在 `experiments/ai-rpg-engine` 中运行 `node --test ancient-card/real-host.test.mjs ancient-card/routes.test.mjs cultivation-card/runtime.test.mjs`。这些测试使用隔离数据目录，不需要模型凭据。插件及共享宿主回归见 `studio/memory-tasks.test.mjs`、`god-card/tests/real-host.test.mjs` 和 `god-card/tests/plugins-http.test.mjs`。

此卡没有数据迁移。需要回退时移除古代卡路由、列表条目、Docker 复制项及受控宿主对 ancient 的允许项，保留独立会话和调用账本以便复核。回退后重新运行现有 RPG 与修仙卡测试。
