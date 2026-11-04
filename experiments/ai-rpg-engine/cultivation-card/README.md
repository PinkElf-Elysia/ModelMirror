# 超级修仙大世界
开场界面位于 `/rpg/cultivation`。复制角色档案后，可先离线核对装配。作者正文仅在 RPG 宿主加载。

剧情使用 RPG 服务间控制面已认证的 google/gemini-3.8-flash。独立宿主通过 RPG_CULTIVATION_ENABLED、RPG_CULTIVATION_CALL_LIMIT 以及既有的 RPG_CONTROL_URL、RPG_S2S_TOKEN 配置。默认关闭，默认额度为零。额度涵盖剧情和记忆。会话和调用回执写入该宿主数据目录的 cultivation 子目录。关闭开关并重启独立宿主即可停用调用，保留数据目录。

建会话时选择是否挂载记忆宫殿。每个挂载回合先生成剧情，再由同一模型提取结构化记忆。续轮保留原始上下文，并将符合关键词的记忆追加到当前行动。已登记且未结清的回合会阻止重放，插件失败时保留已写入的剧情。

离线验证运行 node --test cultivation-card/runtime.test.mjs studio/controlled-provider.test.mjs studio/studio.test.mjs（在 experiments/ai-rpg-engine 下）。本次真实验证通过有界脚本调用同一剧情装配逻辑和 RPG 控制面，中性样例成功；龙婉翩首次尝试上游 HTTP 402。重新认证后，在独立批次完成两轮剧情和两次记忆提取，四笔回执均为 Gemini、HTTP 200、零重试。第二轮请求完整携带首轮原文并召回两条记忆。回执位于本机 .rpg04-work/cultivation-gemini-nov04-resume01/。
