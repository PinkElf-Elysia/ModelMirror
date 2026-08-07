# R1 已知限制

R1 聚焦合同与验证器，以下均为真实限制而非待隐藏能力：

- Creator 只是独立工程空壳，没有编辑、保存、导入、导出或预览游戏能力。
- R1.2 只完成 Authoring Game Pack 结构合同；严格 JSON 解析、引用与图语义验证器将在后续批次实现，Runtime Pack、Compiler 与 Domain Patch 仍不定义。
- 未连接父项目模型、RAG、MCP、Agent、资产或鉴权能力。
- 没有 AI Provider、NPC、3D 场景、Gaussian Splat、Tauri 或部署流程。
- 没有 Godot 项目。Godot 4.6.x 仅由 doctor 作为未来工具检查，缺失不会阻塞 R1。
- 没有父仓路由接入；现有 `/matrix-oasis` 仍是原占位页。
- 没有根 CI 门覆盖本模块。R1 验证从模块根手动运行，并通过拆分演练证明可移植性。
- `apps/creator-web/**` 字节级冻结，其“Game Pack 未定义”等文案是 R0 历史快照，不可用作 R1 能力状态。
- `UNLICENSED` 模块仅供内部实验，不适合作为公开发布物。
- 当前 lockfile 的 `npm audit` 报告间接开发依赖 `esbuild@0.27.7` 存在 1 个 low severity 项（`GHSA-g7r4-m6w7-qqqr`，Windows 开发服务器场景）。R1 继续只允许 loopback 开发/预览，不自动升级固定工具链。

后续轮次若要移除任一限制，必须先定义新一轮边界、验收和回退；不得借 R1 顺手实现。
