# ADR-0006：R5 采用严格本地双文件适配与第三执行器

- 状态：Accepted
- 日期：2026-08-09

## 决策

R5 从冻结 R3 Compiler 接收规范 Runtime Pack JSON 与 Receipt JSON。Godot 只通过两个显式本地路径只读加载，独立完成字节、格式、语义与完整性门，再由不共享 JavaScript evaluator 的 GDScript 执行器推进状态。

Godot 内建 JSON 解析不作为准入权威；R5 实现冻结 0.1.0 格式的专用严格解码器，不实现通用 JSON Schema，也不引入 GDExtension。生成 Pack 不入库，R4 Bootstrap 与全部 R1–R4 权威文件保持冻结。

## 后果

R5 可以用第三执行路径证明 Runtime Pack 的跨语言可执行性，但不建立正式存档、网络传输、3D 玩法或资产协议。孤立 UTF-16 代理项在 Godot 边界明确拒绝；Receipt 仍不是签名。
