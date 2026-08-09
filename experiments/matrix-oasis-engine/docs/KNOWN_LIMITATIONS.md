# R3.1 已知限制

- R3.1 只有治理与隔离基线；Compiler、Runtime Pack/Receipt、Runtime Validator、Runtime Simulator、parity harness 与 Creator 双执行均未实现。
- 当前可运行能力仍是 R2 参考模拟器和最小运行实验台，不能宣称编译态或生产运行时就绪。
- R1/R2 权威输入已冻结；发现缺陷时必须停报，不能混入 R3 修复。
- Runtime Pack 0.1.0、规范 JSON 与 Receipt 的稳定合同要到后续批次才形成。
- 不提供正式存档、回放、undo/redo、自动运行、随机、时间或并发。
- 样例只用于验证，不承诺最终题材、剧情、美术、音频或成品质量。
- 未连接父项目、共享栈、AI、NPC、RAG、MCP、Godot、3D、资产或部署。
- 模块仍依赖手动执行本地门与拆分验证；没有新增根 CI。
- 模块为 `UNLICENSED` 内部实验，不发布 npm 包。
- lockfile 中既有 `esbuild@0.27.7` low severity 开发期问题未在 R3.1 升级；preview 继续限制为 loopback。

移除任一限制必须进入对应批准批次并补齐验收与回退。
