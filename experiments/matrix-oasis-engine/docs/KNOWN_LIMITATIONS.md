# R0 已知限制

R0 有意保持功能极小，以下均为真实限制而非待隐藏能力：

- Creator 只是独立工程空壳，没有编辑、保存、导入、导出或预览游戏能力。
- 未定义 Game Pack、Runtime Pack、Schema、Compiler 或 Domain Patch。
- 未连接父项目模型、RAG、MCP、Agent、资产或鉴权能力。
- 没有 AI Provider、NPC、3D 场景、Gaussian Splat、Tauri 或部署流程。
- 没有 Godot 项目。Godot 4.6.x 仅由 doctor 作为未来工具检查，缺失不会阻塞 R0。
- 没有父仓路由接入；现有 `/matrix-oasis` 仍是原占位页。
- 没有根 CI 门。R0 验证从模块根手动运行，并通过拆分演练证明可移植性。
- `UNLICENSED` 模块仅供内部实验，不适合作为公开发布物。
- 当前 lockfile 的 `npm audit` 报告间接开发依赖 `esbuild@0.27.7` 存在 1 个 low severity 项（`GHSA-g7r4-m6w7-qqqr`，Windows 开发服务器场景）。R0 只允许 loopback 开发/预览，不自动改写用户锁定的版本；进入更高轮次前必须重新审计并由用户批准升级。
- 浏览器人工验收以生产构建 preview 为准；本次内嵌浏览器没有执行 Vite HMR 开发入口，因此开发服务器的首次空白结果未计为通过，生产 preview 已通过桌面与移动验收。

后续轮次若要移除任一限制，必须先定义新一轮边界、验收和回退；不得借 R0 顺手实现。
