# R2 已知限制

R2 聚焦确定性参考语义与最小运行实验台。R2.1 仅完成治理迁移，以下限制必须如实保留：

- R1 Authoring Game Pack 0.1.0、Validator、两个样例及历史验收记录在 R2 字节冻结；发现问题时必须停报，不能在本轮顺手修复。
- R2.1 尚未实现参考模拟器或运行实验台；Creator 仍显示 R0 历史空壳，不能代表当前模块能力。
- 后续参考模拟器只提供纯内存单步会话，不定义 Compiler、Runtime Pack、正式存档、批量回放、随机、时间或并发。
- 会话身份只依赖 Pack format、formatVersion、id 与 contentVersion，不计算内容哈希；作者修改内容时必须提升 contentVersion。
- Creator 后续只允许内置夹具与用户主动选择的本地 JSON，不提供编辑、保存、导出、自动运行、节点图或题材包装。
- “末班地铁：回声十三站”仍是可替换的薄型集成夹具，不承诺最终题材、剧情质量、美术、音频或可玩成品。
- 未连接父项目模型、RAG、MCP、Agent、资产、鉴权、路由、API、数据库或共享栈。
- 没有 AI Provider、NPC、3D、Gaussian Splat、Tauri、Godot 工程或部署流程；Godot 4.6.x 缺失不会阻塞 R2。
- 没有根 CI 门覆盖本模块；验证继续从模块根手动执行，并以历史保留型拆分证明可移植性。
- 模块为 `UNLICENSED` 内部实验，不发布 npm 包。
- 当前 lockfile 仍包含已记录的 `esbuild@0.27.7` low severity 开发期问题；R2 继续只允许 loopback 开发与预览，不自动升级固定工具链。

移除任一限制必须进入明确批次、补齐验收与回退，不能以样例打磨替代引擎主线。
