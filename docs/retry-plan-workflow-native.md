# 原生工作流重试路线归档

> **状态：已完成并被后续实现取代。** 本文记录 2026-06-10 的恢复思路，
> 不是当前排期。当前入口和边界见
> [ARCHITECTURE.md](./ARCHITECTURE.md) 与
> [workflow-native-design.md](./workflow-native-design.md)。

原计划日期：2026-06-10
归档校准日期：2026-07-28

## 当时目标

在不再次污染稳定入口的前提下，按“契约 → 后端执行器 → 独立前端 →
增量节点 → 本地 RAG”逐步恢复原生能力。Dify 在当时只是临时安全网。

## 结果对照

| 原计划阶段 | 当前结果 |
| --- | --- |
| 架构与接口 | 已形成 `WorkflowDefinition`、validate、错误模型和 Harness 规则。 |
| 后端执行器 MVP | classic runner 已支持模型、条件、工具、知识和 Agent Runtime 等节点。 |
| 前端编辑器 MVP | `/workflow` 已成为 classic React Flow 主入口。 |
| 增量节点 | 通过节点 registry、配置面板和专项测试逐步交付；仍需按节点维护。 |
| 本地 RAG | `/rag` 已具备上传、Processor、知识流水线、双索引、评测和人工激活。 |
| 独立实验线 | `/workflow-native` 保留为静态校验与设计实验，不替换 classic runner。 |

因此，原计划中的以下假设已失效：

- `/workflow` 由 Dify iframe 提供。
- `/rag` 需要回退到 Dify 资料库。
- classic 画布只位于 `/workflow/classic`。
- 原生 RAG 仍处于“未来替换”阶段。

## 仍然有效的工程原则

- 先定义公共契约和失败边界，再增加节点。
- 实验能力必须独立、可关闭、可回退。
- 新节点同步修改前端类型、后端 schema、validate、runner、测试和文档。
- 工作流、RAG 和聊天主路径必须分别回归。
- 不把平台级能力压缩成一次不可验收的大重写。

## 后续维护

未来工作流路线不在本文继续追加。新增阶段必须建立独立任务卡，说明用户价值、
运行语义、数据迁移、测试和回退；当前状态更新到
[workflow-native-design.md](./workflow-native-design.md) 或对应模块文档。
