# R6 可玩 3D 威胁模型

## 受保护资产

- 冻结 R1–R5 数据合同、Compiler、Runtime、Creator、Godot Bootstrap、Runtime Lab 与测试证据。
- Runtime snapshot 与 3D 世界投影的原子一致性。
- 仓外 Runtime Pack/Receipt、截图、trace 和日志不进入 Git。

## 主要风险与控制

- 输入注入：只接受固定 InputMap；不读取环境变量、网络或任意文件。
- 交互越权：RayCast3D 只检测碰撞层 3，距离固定 3 m；不可用 action 仍可见但拒绝执行。
- 世界/会话撕裂：先在冻结 R5 Runtime 计算候选结果，成功后一次提交并重建终端；失败保留旧状态。
- 资源膨胀：actions 最大 64，固定 8 列布局，不加载外部模型、贴图、脚本或 PackedScene。
- 题材耦合：第一方源码禁止样例 ID、题材词和专用分支，所有显示文本来自 Runtime inspection。
- 第三方漂移：官方 demo 参考文件非可执行，来源 commit、SHA-256 与 MIT License 精确锁定。
- 物理不确定性：逻辑 trace 要求字节稳定；物理位置只做范围和容差断言，不伪装跨平台浮点一致。

R6 不解决恶意本机进程篡改 Godot、GPU 差异、签名/来源认证、正式存档或网络对手模型。
