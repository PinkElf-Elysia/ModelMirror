# R20任务卡：确定性NPC行为与Godot实体桥

- `R20_BASE_SHA=1ef7b86e4c9d5ab57b5e83fc9e0cadccff14375a`
- 分支：`codex/matrix-oasis-r20-deterministic-npc-bridge`
- worktree：`C:\tmp\modelmirror-matrix-oasis-r20`
- 版本：`0.20.0-r20`
- 七个线性提交；每批先验证再提交。

R20只允许固定策略NPC、R19兼容增量会话、单写loopback协调器、隔离Godot实体桥、测试及本轮文档。Runtime继续是状态转换唯一权威；R16默认Creator与预览保持冻结。

正式退出门仍为：`R20_GODOT_ENTITY_BRIDGE_QUALIFIED`、`R20_MULTI_AGENT_TRACE_DETERMINISTIC`、`R20_RUNTIME_REMAINS_AUTHORITATIVE`。单条时间线在Godot内完成导航、物理与Runtime镜像后，`bridge-report.json`只记录临时事实标识`R20_GODOT_ENTITY_BRIDGE_VERIFIED`；它不能声明正式资格。只有Godot正常退出、实现/二进制/运行工程身份复验、资格凭据和`npc-current.json`事务发布全部成功后，资格CLI才可输出`R20_GODOT_ENTITY_BRIDGE_QUALIFIED`。普通`verify:r20`与负载探针也不得输出或冒充三项正式退出标识。人工验收前不push、不创建PR。

R20.1包含超过5个文件：版本、scope policy、boundary、V2声明、根verify、参考锁和对应反例必须同步迁移，属于同一不可拆分治理目标。后续批次只在本批完整验证通过并本地提交后开始。

R20.4包含超过5个文件：调度器、固定端口loopback协调器、边界扫描精确放行及其出站/通配绑定反例共同构成同一安全目标，拆开会产生可提交但无对应防护或无法通过边界门的中间状态。

R20.5包含超过5个文件：Godot实体桥需要同时加入固定loopback能力、精确Godot边界例外、总边界的静态扫描器护栏及对应范围策略；这些文件是同一fail-closed接线，不能留下“可联网但未受审计”或“桥存在但治理拒绝”的中间提交。

R20.6包含超过5个文件：资格入口、仓外事务存储、预览/捕获命令、协调器异步提交钩子及崩溃/分叉/并发反例共同证明同一条真实链；拆开会留下已返回成功但尚未持久化，或可持久化但无法由真实Godot消费的危险中间状态。

当前阶段：R20.6纠偏、针对性证伪及两份真实缓存自动资格已完成，证据见`docs/rounds/R20_ACCEPTANCE.md`。自动资格不替代人工预览；R20.7尚未开始，只在人工确认中性与末班地铁预览通过后更新状态并形成第七个提交。`docs/V2_STATUS.json`当前结论保持不变。
