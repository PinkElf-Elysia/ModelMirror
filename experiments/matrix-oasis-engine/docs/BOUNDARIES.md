# R12边界

## 允许

- 现有Generator、Assembler、Environment/Spatial包的兼容扩展；
- 明确R12宿主、CLI、测试和文档；
- 指定Creator审批展示与Godot空间wrapper接线；
- 仓外`C:\tmp`提示、供应商资产、资格run、截图和日志；
- 经当次批准的OpenAI官方模型调用，以及另一批次批准的Marble与Meshy有界调用。

## 冻结

R1–R11公共合同、Runtime语义、Scene Pack、examples、历史Creator/Godot模式、vendor、ADR和验收记录均冻结。精确allowlist优先于冻结根，未知路径fail-closed。

schema v12固定`activeRound=R12`和基线`6a88c648f3db2afc39574a57066a14c341c161f9`。所有父仓路径、Docker、共享栈、父服务、数据库和父凭据存储禁止修改或依赖；普通验证禁止供应商网络调用。

初版状态只能由`docs/MVP_STATUS.json`、R12验收记录和`check:mvp-claim`共同改变；任何单独文案声明均视为治理失败。
