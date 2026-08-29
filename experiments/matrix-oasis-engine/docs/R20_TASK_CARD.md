# R20任务卡：确定性NPC行为与Godot实体桥

- `R20_BASE_SHA=1ef7b86e4c9d5ab57b5e83fc9e0cadccff14375a`
- 分支：`codex/matrix-oasis-r20-deterministic-npc-bridge`
- worktree：`C:\tmp\modelmirror-matrix-oasis-r20`
- 版本：`0.20.0-r20`
- 七个线性提交；每批先验证再提交。

R20只允许固定策略NPC、R19兼容增量会话、单写loopback协调器、隔离Godot实体桥、测试及本轮文档。Runtime继续是状态转换唯一权威；R16默认Creator与预览保持冻结。

退出门：`R20_GODOT_ENTITY_BRIDGE_QUALIFIED`、`R20_MULTI_AGENT_TRACE_DETERMINISTIC`、`R20_RUNTIME_REMAINS_AUTHORITATIVE`。人工验收前不push、不创建PR。

R20.1包含超过5个文件：版本、scope policy、boundary、V2声明、根verify、参考锁和对应反例必须同步迁移，属于同一不可拆分治理目标。后续批次只在本批完整验证通过并本地提交后开始。

R20.4包含超过5个文件：调度器、固定端口loopback协调器、边界扫描精确放行及其出站/通配绑定反例共同构成同一安全目标，拆开会产生可提交但无对应防护或无法通过边界门的中间状态。

当前阶段：R20.1治理迁移与来源二次核查进行中。R20.7只在人工确认中性与末班地铁预览通过后更新V2状态并形成第七个提交。
