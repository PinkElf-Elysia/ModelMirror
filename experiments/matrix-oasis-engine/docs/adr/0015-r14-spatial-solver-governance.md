# ADR-0015：R14确定性空间求解与最终物理复验

- 状态：Accepted
- 日期：2026-08-17

## 背景

R13将语义意图和环境事实拆为可独立复验输入，但产品仍使用历史布局。R12人工验收表明出生点、资产与Action terminal错位属于系统性求解缺失，继续题材调参无法证明泛化。

## 决策

R14新增独立Solution合同、确定性有界求解器和Godot最终复验器。求解器以单一可玩Navigation component、确定性zone播种、多源domain、稳定候选排序和有界DFS满足全部硬约束；无解或达到搜索上限时静态失败，不回退旧AABB布局。

Node结果必须由隔离Godot场景加载真实collider、Meshy GLB和Action terminal，等待导航与物理同步后复验路径、capsule、接地、穿透、重叠和视线。只有复验成功的Solution才能进入独立solved overlay。

开发期保持旧Creator默认路径。R11中性案例与R12末班地铁均人工通过后，R14.7才切换默认预览并解除MVP声明门。

## 后果

空间选择、求解证明与真实物理证据可分别审计，且不再依赖题材坐标。代价是R14 profile有明确容量上限，复杂场景会fail closed；Godot 4.6.3 Windows仍是锁定资格工具链。

## 回退

逆序revert R14七个提交。solved overlay与current独立于R10–R13 run；仓外facts、solution、capture与run需按清单单独处理。
