# 模块边界

## R6 允许范围

- 只修改 `experiments/matrix-oasis-engine/**`。
- R1–R5 Creator、examples、packages、Bootstrap、Runtime、Runtime Lab、GdUnit4、历史 ADR/验收与语义测试全部冻结。
- 新 Godot 内容只允许在 `apps/runtime-godot/playable/**` 与 `apps/runtime-godot/test/r6/**`。
- `apps/runtime-godot/project.godot` 是唯一冻结例外，只允许增加已批准的 InputMap、Jolt 与物理插值设置；主场景和显示设置不得改变。
- 官方 Godot demo 参考只允许在 `third-party/godot-demo-projects/**` 以非可执行扩展、MIT License、锁文件和适配说明保存。
- Godot 二进制、缓存、导出模板、PCK、图形证据和生成的 Pack/Receipt 均只放仓外。

## 运行与物理边界

- Godot 固定 4.6.3、Forward+、Jolt、60 Hz 物理更新和物理插值。
- 世界碰撞层为 1、玩家为 2、交互 Area 为 3；相机中心射线只检测 Area 层，最大 3 m。
- 第一人称控制只含 WASD/方向键、鼠标观察、E/Enter、Esc 和左键重新捕获；无跳跃、冲刺、蹲伏或手柄。
- Action 终端完全由当前 Runtime inspection 派生，最多 64 个，按声明顺序和确定性 8 列网格生成；不得出现样例专用分支。
- action、ending 与 reset 只调用冻结 R5 Runtime；失败必须保留 snapshot、HUD、终端和玩家状态。

## 能力与供应链边界

- 第一方 GDScript 禁止网络、Socket、`OS.execute`、环境变量、动态脚本加载、模块外路径和文件写入。
- GdUnit4 不套用第一方能力扫描，继续由冻结 vendor integrity 约束。
- 官方 demo 参考不进入 Godot 资源扫描，不参与运行；其来源 commit、文件 SHA-256、License 与适配说明由独立锁验证。
- 固定帧捕获只能写入 `C:\tmp` 新目录，不纳入自动 `verify`，也不建立跨 GPU 像素 golden。

## 自动范围门与回退

- schema v6 固定 `activeRound=R6` 与基线 `430f24a4fd8510a0d54f14bcd240a80423d16719`。
- round scope 同时检查 committed、staged、unstaged、untracked；冻结路径优先、未知路径失败关闭。
- parent scope 拒绝全部模块外变化；standalone 只在模块等于仓库根时返回 not-applicable。
- R6 不启动父服务、Docker 或共享栈。每批可逆序 revert，整体回退后恢复完整 R5。
