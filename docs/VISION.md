# ModelMirror 产品愿景

> 从寻找一个模型，到编译一套智能。<br>
> From choosing a model to compiling intelligence.

本文面向生态伙伴、核心贡献者、长期开发者和关注平台方向的读者。它描述 ModelMirror 的市场判断、品牌叙事与演进方向，不替代[当前系统架构](./ARCHITECTURE.md)，也不把目标架构表述为已实现产品。

最后更新日期：2026-08-09
维护人：模镜团队

## Executive Summary

ModelMirror 当前是一个可本地部署的 AI 资源发现、比较、调用与组合工作台，面向模型、Agent、Skill、MCP、知识库、数据分析和工作流。用户可以浏览资源，使用原生模型路由与多模态聊天，连接工具、安装 Skill、构建经典工作流和知识流水线，并通过 Data X 与 Agent Studio 完成本地受控任务。

项目正在探索一种新的产品类别：**AI Capability Compiler（AI 能力编译器）**。它不只回答“使用哪个模型”，而是尝试把用户目标转换为结构化能力需求，再映射到可执行、可观察、可评测的模型、工具、知识、Agent 和工作流组合。

商业方向是成为 Agent 经济中的**中立 AI 能力控制平面与智能分配层**；长期研究愿景是 **AI Capability OS / Self-Evolving Meta-System**。这里的“中立”指架构目标不绑定单一模型供应商或 Agent 框架，不构成对商业独立性、合规性或生产成熟度的承诺。

## Why Now

AI 供给正在从少数通用模型，扩展为由模型、工具、知识库、Skill、MCP、垂类 Agent 和多 Agent 工作流组成的异构生态。资源越丰富，发现成本、组合成本和验证成本也越高。

新的瓶颈不再只是“是否拥有模型”，而是：

- 一个任务到底需要哪些能力和多高复杂度；
- 何时使用工具、知识库、单 Agent 或多 Agent；
- 如何在质量、成本、时延、可靠性和风险之间权衡；
- 如何证明结果满足要求；
- 如何让每次执行反哺下一次选择和编排。

模型网关解决模型调用，Agent 框架解决 Agent 构建，MCP Registry 解决工具发现，工作流平台解决流程编排，资源目录解决浏览与比较。ModelMirror 不把这些生态角色视为必须替代的竞争对象，而是探索横跨它们的统一描述、分配、执行和反馈层。

## 从资源发现到智能分配

ModelMirror 的演进不是从“目录”直接跳到“操作系统”，而是沿着可验证的产品链路逐步推进：

```text
发现资源
→ 比较与试用
→ 组合模型、工具、知识和 Agent
→ 将目标表达为结构化能力需求
→ 按约束选择执行路径
→ 记录与评测结果
→ 改进下一次路由和编排
```

资源发现是入口，不是终局；AI Capability Compiler 是目标产品引擎；Capability Graph、经评测的 Execution Traces 和 Routing Policy Intelligence 才可能形成长期复利。

## 四层定位

| 层级 | 定位 | 作用 | 成熟度 |
| --- | --- | --- | --- |
| 用户入口 | **“AI 牛马招聘会”** | 用候选人、技能和面试间等隐喻降低资源发现门槛 | Available Today |
| 产品类别 | **AI Capability Compiler / AI 能力编译器** | 把目标转换为能力需求和执行组合 | Target Architecture |
| 商业定位 | **AI Capability Control Plane / AI 能力控制平面** | 跨供应商、模态、工具和 Agent 的智能分配与治理层 | Strategic Direction |
| 长期愿景 | **AI Capability OS / Self-Evolving Meta-System** | 组织可发现、可组合、可评测、可演化的智能网络 | Research Direction |

“AI 牛马招聘会”保留为产品入口和体验隐喻，但不承担最高层级的商业定位。资源目录承担发现入口；能力编译、执行反馈与策略学习是越过聚合目录所需的目标能力。

## 品牌故事：从看见 AI 到编译 AI

ModelMirror 最初是一面映照模型生态的“镜子”，帮助用户看见、比较和试用不同模型。

当 AI 进入模型、工具、知识和 Agent 协作的阶段，这面镜子的价值也从“展示资源”转向“映射能力”：理解一个资源真正能做什么、和什么兼容、如何组合、执行效果如何，以及下一次怎样选择得更好。

因此，ModelMirror 正从“看见 AI”走向“编译 AI”。品牌承诺可以概括为：

> 让每个人不必理解整个 AI 生态，也能调动整个 AI 生态。

这是一项目标，不是对当前自动化程度的夸大承诺。

## 初始价值场景

### 当前可验证场景

- **模型发现、路由与试用**：按能力、价格等信息筛选候选模型，或通过原生 Model Router 选择策略后进入多模态聊天。
- **分入口能力组合**：在 Chat、RAG、Data X、Agent Studio 和经典 Workflow 等现有入口中，分别使用 MCP、Skill、知识库、指标或工作流节点完成任务；当前尚未形成 Capability IR 驱动的统一跨资源编排。
- **从目标到工作流草稿**：Meta-Agent 生成并静态校验经典工作流草稿，保留人工编辑与运行入口。
- **运行观测**：查看轻量 Run、Checkpoint、工具事件和审计摘要；当前主要是本地、内存态能力。

### 目标场景

- 用户描述任务和约束，系统生成 Capability IR，而不是只做关键词意图分类。
- Meta Router 根据质量、成本、时延、可靠性和能力匹配选择路由策略与路由器组合。
- 执行结果进入统一评测，形成可审计的 Execution Trace，并更新后续策略。
- 开发者把模型、Skill、MCP、知识库、Agent 或工作流注册为标准化能力资产。

目标场景的完整设计见 [AI Capability Compiler 目标架构](./architecture/ai-capability-compiler.md)。

## ModelMirror 在生态中的位置

| 生态角色 | 主要解决的问题 | ModelMirror 的目标关系 |
| --- | --- | --- |
| 模型供应商与 OpenAI 兼容网关 | 提供模型与统一调用接口 | 注册、比较和路由的上游能力供给 |
| Agent 框架 | 构建 Agent、工具循环与协作逻辑 | 可接入的执行框架与 Agent 资产来源 |
| MCP Server 与 Registry | 工具协议、连接和发现 | 工具能力来源与运行时连接对象 |
| 知识库与 RAG 系统 | 文档处理、检索和引用 | 可路由的知识能力与上下文来源 |
| Workflow 平台 | 节点编排和运行管理 | 可执行流程来源、互操作对象或底层运行器 |
| Benchmark / Harness | 能力测量、回归与门禁 | 评测信号与安全演进基础 |

ModelMirror 的目标不是把所有底层能力重新实现一遍，而是建立可描述、可选择、可组合、可观测和可治理的连接层。

## 目标生态飞轮

以下是目标复利机制，不代表相关数据资产已经形成：

```text
更多生态资源
→ 更完整的 Capability Registry / Capability Graph
→ 更多真实任务与经评测的 Execution Traces
→ 更准确的评测、路由和组合策略
→ 更好的任务结果
→ 更多用户与开发者参与
→ 更多资源、任务与反馈
```

飞轮成立依赖三个前提：资源描述可信、执行轨迹可审计、评测信号能代表真实任务。只有资源数量而没有质量与反馈，不构成复利。

## 四项长期复利资产

### Capability Graph

目标是沉淀任务、能力、资源、兼容关系、约束和有效组合之间的关系。它不同于静态资源列表，也不能由标签数量替代。

### Execution Trace Dataset

随着真实执行和评测覆盖扩大，逐步记录任务、分类、路由、调用、结果、反馈和修复。只有经过权限控制、脱敏和质量校验的轨迹才可进入长期数据资产。

### Routing Policy Intelligence

从经评测轨迹中学习：在不同质量、成本、时延、可靠性和风险要求下，应选择哪些策略与能力组合。当前局部规则路由与 Fusion 实验不等同于完整 Routing Policy Intelligence。

### Meta Capability Evolution

长期目标是持续生成和改进 Prompt、Skill、MCP、Agent 与 Workflow。它必须由评测、权限、测试和发布门禁约束，不能被理解为无限或无人监管的自我修改。

## What ModelMirror Is / Is Not

| ModelMirror 的当前形态与目标方向 | 不应被理解为 |
| --- | --- |
| **当前：** 跨模型、工具、知识与 Agent 的资源发现和组合工作台 | 只有链接和排行榜的 AI 聚合网站 |
| **目标：** 面向统一能力描述、路由与评测的控制层 | 已完成的企业级、多租户调度控制平面 |
| **方向：** 连接模型网关、Agent 框架、MCP 和 Workflow 平台的中立架构 | 声称替代所有模型供应商和 Agent 框架的封闭平台 |
| **研究：** 通过 Harness、Trace 和人工门禁推进的受控演进 | 无约束、无人干预地修改和部署自身的系统 |
| **研究：** 压缩经评测路由、规划和工具策略 | 复制第三方闭源模型权重的蒸馏项目 |

## 当前阶段与长期路线

### Now：发现、试用与本地组合

- 维护模型、Agent、MCP、Skill、Prompt 与 Plugin 等资源入口；
- 加固原生模型路由、多模态聊天、经典工作流、本地知识流水线、Data X、Agent Studio 和 MCP Runtime；
- 明确实验模块、持久化边界和真实供应商验收状态。

### Next：统一描述与可观察执行

- 设计 Universal AI Asset Schema 和版本化 Capability Registry；
- 统一 Run、Trace、Checkpoint、Evaluation 与安全审计的最小契约；
- 以小范围任务验证 Capability IR 和策略路由，不提前建设完整联邦。

### Later：策略联邦与元能力

- 分离 Model、Provider、Skill、MCP、RAG 和 Handoff Router；
- 由 Meta Router 按约束选择策略与组合；
- 用评测结果改进 Prompt、Agent、Skill 和 Workflow 生成。

### Research：能力内核与受控演进

- 研究如何将经验证的路由、规划、工具使用、记忆和评测策略压缩为 AI Capability Kernel；
- 通过 Observe → Diagnose → Coding Agent → PR/Test → Deploy/Ops 门禁实现受控 System Evolution；
- 任何自动变更都必须可解释、可测试、可回退、可审批。

路线阶段不构成时间承诺。每一阶段都应以仓库证据和验收门禁更新状态。

## 产品与架构原则

1. **事实先于叙事**：当前能力以代码、测试、配置和可复现验证为准。
2. **目标与现状分离**：目标架构用未来时和状态标签表达。
3. **渐进复杂度**：优先使用满足任务的最小能力层级，再有证据地升级。
4. **生态互操作**：连接并增强现有生态，让用户不必先理解所有底层系统。

## 相关阅读

- [README](../README.md)
- [当前系统架构](./ARCHITECTURE.md)
- [AI Capability Compiler 目标架构](./architecture/ai-capability-compiler.md)
- [术语表](./GLOSSARY.md)
- [Harness Engineering](./HARNESS_ENGINEERING.md)
