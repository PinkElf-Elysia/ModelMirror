# ADR-0016：R15实际输入重放与运行证据闭环

- 状态：Accepted
- 日期：2026-08-21

## 背景

R14已经以真实Godot物理和导航发布可验证Solution，但其图形捕获仅覆盖启动固定帧，不能证明用户经控制器、射线和终端实际到达ending、循环与reset。

## 决策

R15复用R14作为静态权威，不新建第二套Verifier。Runtime Simulator只规划通用路径；Godot通过`Input.parse_input_event()`驱动全局InputMap键盘状态，并通过目标Viewport的`push_input()`重放保存的鼠标按键与移动，使输入进入实际控制器、射线和terminal链。Evidence同时绑定逻辑投影、物理checkpoint、逐节点媒体和独立实时性能。

失败只允许将精确物理诊断映射为R14候选键，最多排除两轮并完整重求解、复验和重放。无法映射的故障不自动修改语义或代码。

## 后果

构建成功与实际可玩证据被明确分离，且资格不依赖桌面键鼠脚本。R15仍不证明Creator可复现；该迁移保留给R16。
