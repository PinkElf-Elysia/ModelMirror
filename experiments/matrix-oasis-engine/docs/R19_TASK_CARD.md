# R19任务卡：NPC权威合同与确定性裁决

- `R19_BASE_SHA=821067a7db4811a3f3f1fd649e4fdfade9eafb22`
- 分支：`codex/matrix-oasis-r19-npc-authority-contracts`
- worktree：`C:\tmp\modelmirror-matrix-oasis-r19`
- 版本：`0.19.0-r19`
- 六个线性提交；每批先验证再提交。

R19只允许新增NPC权威合同、纯函数Ledger/裁决运行时、离线CLI、测试及本轮文档。Runtime继续是状态转换唯一权威；R19不修改Creator、Godot、现有Pack或任何供应商适配器。

退出门：`R19_ADJUDICATION_FAIL_CLOSED`、`R19_LEDGER_REBUILD_DETERMINISTIC`、`R19_CONTRACTS_CANONICAL`。人工验收前不push、不创建PR。

R19.1包含超过5个文件：版本、scope policy、boundary、V2声明、根verify与对应反例必须同步迁移，属于同一不可拆分治理目标。后续批次仅在本批完整verify通过并本地提交后开始。

当前阶段：R19.1–R19.5已形成五个线性本地提交；R19.6实现、全量回归、父client和中性CLI证据已完成。第六个本地提交后从clean HEAD执行standalone extraction并记录仓外source/split/tree/archive哈希，随后进入人工验收。人工验收前不push、不创建PR。
