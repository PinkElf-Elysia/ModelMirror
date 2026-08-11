# ADR-0007：R6 第一人称可玩 3D 骨架治理

状态：已接受
日期：2026-08-10

## 决策

R6 以冻结 R5 Godot Runtime 为唯一语义入口，新建独立 `playable` 场景与测试，不修改 R5 Runtime/HUD 或 R4 Bootstrap。只对 `project.godot` 开放 InputMap、Jolt 和物理插值的精确变更。

控制器使用 CharacterBody3D 与 Godot 标准 API；不 vendoring 社区 FPS 插件。官方 MIT demo 仅以不可执行参考文件、License、锁和适配说明保存，正式实现保持独立且可测试。

Runtime actions 以确定性通用终端呈现，不新增 3D 绑定 Schema。这样 R6 能验证可玩闭环，同时保留后续正式场景绑定与资产管线的设计空间。

## 后果

- 可以删除 `playable`、R6 测试/脚本、参考目录并还原 `project.godot` 来完整回退。
- R6 的空间布局不是内容合同，不能被后续工具当作稳定关卡格式。
- NPC、导航、资产、动画、音频、存档、AI 和父项目接入继续延后。
