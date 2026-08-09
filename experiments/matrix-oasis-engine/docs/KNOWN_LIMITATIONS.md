# R3.5 已知限制

- Runtime Pack/Receipt 0.1.0 合同、canonical-json/1、严格 Validator、确定性 Compiler、安全 CLI、独立 Runtime Simulator、parity harness 与 Creator 锁步实验台已实现；仍不是 Godot 或生产引擎运行时。
- 当前语义等价证据来自冻结夹具、全部 condition/effect/target、精确轨迹、错误路径与有界可达状态探索；它不是对任意未来格式、无限状态空间或生产运行时的形式化证明。
- Runtime 快照是绑定 source/artifact 哈希的实验内存交换形状，不是正式存档；不承诺跨版本迁移、长期兼容或脱离对应 prepared handle 使用。
- R1/R2 权威输入已冻结；发现缺陷时必须停报，不能混入 R3 修复。
- Compiler 会从完整规范 Authoring 内容生成 `source.canonicalSha256`；但下游只有 Runtime Pack/Receipt 时仍无法独立认证作者或来源，Receipt 也不是签名。
- CLI 的 FileHandle 与目录 rename 是同卷、同父目录的最小发布边界；它降低半成品和内容越界风险，但 Node 无可移植 `openat`，不宣称跨所有文件系统、网络盘或恶意同用户宿主具备数据库事务语义。身份门与 open 之间最多仍可能留下外部零字节文件；最终成功返回后 Artifact 也不是持续锁定的。
- Receipt 只提供字节一致性，不是签名、身份或可信编译器证明；恶意方可同时替换 Pack 与 Receipt。
- canonicalizer 无法可靠识别所有透明 JavaScript Proxy；trap 故障会安全地转为静态 operational error。
- parity harness 在差异时失败关闭，但不自动生成最小反例、持久化 trace 或修复 Artifact；这些诊断工具不属于 R3.5。
- Creator 只支持内置或 1 MiB 内本地 Authoring JSON、单步执行、重置、状态观察与显式下载；不提供编辑、保存、导出 Authoring、回放、批量执行或 stepLimit 设置界面。
- 浏览器下载使用临时 object URL；它只导出当前规范字节，不验证下载目录、后续文件篡改或来源真实性。
- 不提供正式存档、回放、undo/redo、自动运行、随机、时间或并发。
- 样例只用于验证，不承诺最终题材、剧情、美术、音频或成品质量。
- 未连接父项目、共享栈、AI、NPC、RAG、MCP、Godot、3D、资产或部署。
- 模块仍依赖手动执行本地门与拆分验证；没有新增根 CI。
- 模块为 `UNLICENSED` 内部实验，不发布 npm 包。
- lockfile 中既有 `esbuild@0.27.7` low severity 开发期问题未在 R3.1 升级；preview 继续限制为 loopback。

移除任一限制必须进入对应批准批次并补齐验收与回退。
