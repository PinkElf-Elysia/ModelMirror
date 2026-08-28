# R19任务卡：NPC权威合同与确定性裁决

- `R19_BASE_SHA=821067a7db4811a3f3f1fd649e4fdfade9eafb22`
- 分支：`codex/matrix-oasis-r19-npc-authority-contracts`
- worktree：`C:\tmp\modelmirror-matrix-oasis-r19`
- 版本：`0.19.0-r19`
- 六个线性提交；每批先验证再提交。

R19只允许新增NPC权威合同、纯函数Ledger/裁决运行时、离线CLI、测试及本轮文档。Runtime继续是状态转换唯一权威；R19不修改Creator、Godot、现有Pack或任何供应商适配器。

退出门：`R19_ADJUDICATION_FAIL_CLOSED`、`R19_LEDGER_REBUILD_DETERMINISTIC`、`R19_CONTRACTS_CANONICAL`。人工验收前不push、不创建PR。

R19.1包含超过5个文件：版本、scope policy、boundary、V2声明、根verify与对应反例必须同步迁移，属于同一不可拆分治理目标。后续批次仅在本批完整verify通过并本地提交后开始。

当前阶段：R19.1治理验证中，六个实现批次尚未全部交付；不将治理通过或已有Runtime绿测记为NPC裁决成功。
