# ADR-0004：R3 采用不可变 Runtime Pack 与独立双执行治理

- 状态：已接受（R3.1）
- 日期：2026-08-08

## 背景

R1 已冻结 Authoring 合同与验证语义，R2 已提供确定性参考执行。R3 需要在不改变这些权威输入、不提前接入 Godot 的前提下，证明 Authoring 内容可确定性编译并在独立执行器中保持行为等价。

## 决策

- 建立可读、规范 JSON 的 Runtime Pack 0.1.0 与独立 Receipt；字段和规范化规则在 R3.2 后才冻结。
- Compiler、Runtime Simulator 与 parity harness 使用独立私有 workspace，保持浏览器兼容和无父依赖。
- Runtime Simulator 不共享 R2 evaluator；parity harness 只调用两个包根公开 API。
- R1/R2 权威路径字节冻结，schema v3 对既有文件精确放行、只对五个新 package 放行前缀。
- R3.1 仅落实治理、威胁模型和验收骨架，不实现 Runtime API。

## 结果

后续批次可分别验证合同、编译、独立执行和 UI 锁步，并能单批回退。任何冻结输入变更、模块外依赖或未列入白名单的新路径都必须停止并重新审批。

## 被拒绝方案

- 直接把 R2 evaluator 复用为 Runtime：无法证明编译前后独立等价。
- 先定义 Godot 专属或二进制格式：会过早锁定宿主与优化 ABI。
- 继续放行整个 app/docs/scripts/tests 目录：无法对并行工作树中新路径失败关闭。
