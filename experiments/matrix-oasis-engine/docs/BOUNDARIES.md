# R13边界

## 允许

- 新的Spatial Intent与Environment Facts合同和严格验证器；
- 新的离线Node harness与`apps/runtime-godot/spatial_analysis/`分析场景；
- 固定commit、许可和哈希的非执行参考摘录；
- 仓外`C:\tmp`分析输入、facts、调试捕获和日志。

## 冻结

R1–R12公共合同、Runtime语义、Scene/Spatial格式、examples、Creator、现有Godot产品场景、vendor、ADR和验收记录均冻结。精确allowlist优先于冻结根，未知路径fail-closed。

schema v13固定`activeRound=R13`和基线`77ec8c4eace9f8dbd1dd119cd70727570bd99e9a`。所有父仓路径、Docker、共享栈、父服务、数据库和父凭据存储禁止修改或依赖；R13全部执行路径禁止网络和供应商调用。

R13不允许求解、产品接线或案例坐标。初版状态保持`pending-spatial-solver`且`claimAllowed=false`；任何单独文案声明均视为治理失败。
