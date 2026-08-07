# ADR-0003：R2 采用确定性参考模拟器与正向范围策略

- 状态：已接受（R2.1）
- 日期：2026-08-07

## 背景

R1 已冻结 Authoring Game Pack 0.1.0 与确定性 Validator，但书面 condition、effect 和迁移语义尚无可执行参考。直接冻结 Compiler 或 Runtime Pack 会让未验证的运行语义过早进入长期格式。

## 决策

- R2 只建设案例无关的纯内存参考模拟器和 Creator 最小运行实验台。
- Compiler、Runtime Pack、正式存档、回放协议、Godot 与父项目适配继续延期。
- R1 contracts、validator、examples 及 R0/R1 验收记录相对 R2 固定基线字节冻结。
- 本轮范围由机器可读正向 allowlist 控制；冻结路径优先，未知模块路径与全部模块外路径失败关闭。
- Creator 只在后续批准批次解冻，不接网络、环境变量、持久化或父项目能力。

## 结果

R2 可以把 R1 语义变成可重复验证的参考答案，未来 Compiler/Runtime Pack 必须以该参考行为做差分验收。任何需要修改冻结 R1 输入的发现都必须停止 R2 并另行审批。

## 被拒绝方案

- R2 同时完成 Compiler、Runtime Pack 与状态机：范围过大，并会提前冻结执行 ABI。
- 只做静态 Inspector：无法验证 condition、effects 和迁移语义。
- 在 R2 内兼容修复 R1：会混合轮次责任并使既有人工验收失效。
