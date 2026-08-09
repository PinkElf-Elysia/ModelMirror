# R3.3 已知限制

- Runtime Pack/Receipt 0.1.0 合同、canonical-json/1、严格 Validator、确定性 Compiler 与安全 CLI 已实现；Runtime Simulator、parity harness 与 Creator 双执行仍未实现。
- Compiler 只证明 Authoring 到规范 Runtime Artifact 的确定性映射与完整性回验；没有独立编译态执行器，因此不能宣称编译前后语义等价或生产运行时就绪。
- R1/R2 权威输入已冻结；发现缺陷时必须停报，不能混入 R3 修复。
- Compiler 会从完整规范 Authoring 内容生成 `source.canonicalSha256`；但下游只有 Runtime Pack/Receipt 时仍无法独立认证作者或来源，Receipt 也不是签名。
- CLI 的 FileHandle 与目录 rename 是同卷、同父目录的最小发布边界；它降低半成品和内容越界风险，但 Node 无可移植 `openat`，不宣称跨所有文件系统、网络盘或恶意同用户宿主具备数据库事务语义。身份门与 open 之间最多仍可能留下外部零字节文件；最终成功返回后 Artifact 也不是持续锁定的。
- Receipt 只提供字节一致性，不是签名、身份或可信编译器证明；恶意方可同时替换 Pack 与 Receipt。
- canonicalizer 无法可靠识别所有透明 JavaScript Proxy；trap 故障会安全地转为静态 operational error。
- 不提供正式存档、回放、undo/redo、自动运行、随机、时间或并发。
- 样例只用于验证，不承诺最终题材、剧情、美术、音频或成品质量。
- 未连接父项目、共享栈、AI、NPC、RAG、MCP、Godot、3D、资产或部署。
- 模块仍依赖手动执行本地门与拆分验证；没有新增根 CI。
- 模块为 `UNLICENSED` 内部实验，不发布 npm 包。
- lockfile 中既有 `esbuild@0.27.7` low severity 开发期问题未在 R3.1 升级；preview 继续限制为 loopback。

移除任一限制必须进入对应批准批次并补齐验收与回退。
