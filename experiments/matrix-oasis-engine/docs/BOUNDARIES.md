# 模块边界

## R7 允许范围

- 只修改 `experiments/matrix-oasis-engine/**`。
- R1–R6 Creator、既有 examples/packages、Bootstrap、Runtime、playable、GdUnit4、历史 ADR/验收与语义测试全部冻结。
- 新 Godot 内容只允许在 `apps/runtime-godot/scene_binding/**` 与 `apps/runtime-godot/test/r7/**`。
- `apps/runtime-godot/project.godot` 完全冻结；R7 使用独立场景和 CLI 参数。
- 官方 Godot demo 参考只允许在 `third-party/godot-demo-projects/**` 以非可执行扩展、MIT License、锁文件和适配说明保存。
- Godot 二进制、缓存、导出模板、PCK、图形证据和生成的 Pack/Receipt 均只放仓外。

## 场景与资产边界

- Scene Pack 最大 256 KiB，只接受 canonical JSON 与本地 `.glb`；禁止 archive、SPZ、网络 URI、外部 buffer/texture、绝对路径和链接。
- 单资产最大 32 MiB、总资产 128 MiB、最多 16 assets/128 placements/4096 node bindings。
- 所有空间数据使用毫米、毫度与千分比安全整数；不增加浮点 canonical profile。
- collider 必须显式引用；静态 collider 保持世界层 1，R6 玩家/交互层不变。
- 场景、Runtime action、ending 与 reset 候选全部成功后才一次提交；失败必须保留旧世界、snapshot、HUD、终端和玩家状态。

## 能力与供应链边界

- 第一方 GDScript 禁止网络、Socket、`OS.execute`、环境变量、动态脚本加载、模块外路径和文件写入。
- GdUnit4 不套用第一方能力扫描，继续由冻结 vendor integrity 约束。
- 官方 demo 参考不进入 Godot 资源扫描，不参与运行；其来源 commit、文件 SHA-256、License 与适配说明由独立锁验证。
- 固定帧捕获只能写入 `C:\tmp` 新目录，不纳入自动 `verify`，也不建立跨 GPU 像素 golden。
- Marble/Meshy 真实调用、额度查询、任务轮询和下载在 R7 全部禁止；gdgs 只在仓外固定 commit 副本资格验证。

## 自动范围门与回退

- schema v7 固定 `activeRound=R7` 与基线 `a4a2a68d2fc5cf056c741cd3101fcf36a250ad6e`。
- round scope 同时检查 committed、staged、unstaged、untracked；冻结路径优先、未知路径失败关闭。
- parent scope 拒绝全部模块外变化；standalone 只在模块等于仓库根时返回 not-applicable。
- R7 不启动父服务、Docker 或共享栈。每批可逆序 revert，整体回退后恢复完整 R6。
