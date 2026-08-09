# 模块边界

## R4 允许范围

- 只修改 `experiments/matrix-oasis-engine/**`。
- R1–R3 Creator、examples、packages、历史 ADR/验收与语义测试全部冻结。
- 新 Godot 内容只允许在 `apps/runtime-godot/**`；第三方记录只允许在 `third-party/**`。
- `project.godot` 只能位于 Godot 根；第一方 `.gd/.gdshader/.tscn/.tres` 只能位于该工程。
- 唯一 addon 根是 `apps/runtime-godot/addons/gdUnit4/**`；其他 addon 路径失败关闭。

## 工具与供应链

- Node 24.x、npm 11.x、Git 与 Godot 4.6.3 是 R4 必需工具。
- Godot 可执行文件和导出模板不得进入仓库；通过仓外 `GODOT_BIN` 使用。
- GdUnit4 由机器可读 lock 固定 upstream、tag、commit、MIT、源归档哈希和目录树哈希；源码保持未修改。
- 生成的 `.godot/`、test reports、movie captures、logs 与 exports 均忽略且不得跟踪。

## 能力边界

- 第一方 GDScript 禁止网络、Socket、`OS.execute`、环境变量、动态脚本加载、模块外路径和未批准写入。
- GdUnit4 不套用第一方能力扫描，改由 vendor integrity 约束。
- MCP 只在仓外一次性副本上使用 loopback、无凭据验证；正式工程不包含 MCP addon、配置或运行依赖。
- 固定帧捕获必须输出到仓外临时目录，不能写入模块或父仓。

## 自动范围门与回退

- schema v4 固定 `activeRound=R4` 与基线 `df4a4b53e1f03f81fbf5a041065dc1443158c472`。
- round scope 同时检查 committed、staged、unstaged、untracked；冻结路径优先、未知路径失败关闭。
- parent scope 拒绝全部模块外变化；standalone 只在模块等于仓库根时返回 not-applicable。
- R4 不启动父服务、Docker 或共享栈。每批可逆序 revert，整体回退后恢复完整 R3。
