# ADR-0014：R13空间语义事实底座

- 状态：Accepted
- 日期：2026-08-16

## 背景

R12证明自然语言、供应商资产、Pack和Godot运行时可以技术贯通，但人工验收持续发现出生点、资产、终端和碰撞区域错位。继续针对单一地铁环境调坐标无法证明可泛化，根因是现有组装器把语义意图、环境测量和最终布局混在同一阶段，且缺少可独立复验的空间事实。

## 决策

R13引入两个独立合同：Spatial Intent只描述zone、placement、可见性、support、facing、near、separate和clearance等语义；Environment Facts由隔离Godot分析器从已验证collider、NavigationMesh和物理query中提取。两者都使用canonical JSON、整数单位、严格身份和稳定顺序。

分析器固定Godot 4.6.3、右手Y-up、毫米、Euler YXZ，以及半径350 mm、高度1800 mm、floor snap 200 mm、最大坡度45°的玩家profile。GLTF与导航source geometry解析在主线程执行，导航使用异步烘焙，ray/capsule查询在物理同步阶段执行。

Godogen、Holodeck、ProcTHOR与GameCraft-Bench只作为固定commit的非执行参考摘录；不引入它们的运行时或依赖。R13不修改R8生成器输出Spatial Intent，不实现约束求解，不切换Creator或现有预览。

## 后果

R14获得可分别审计的意图与事实输入，可以实现确定性约束求解和最终物理复验。代价是R13本身不会改善用户可见布局，且facts确定性只在锁定Godot/Windows工具链内承诺。

## 回退

逆序revert R13六个提交即可恢复R12状态。新增包、分析场景和参考摘录均无运行时消费者；仓外facts与调试证据需按验收清单另行处理。
