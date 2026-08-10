# 模镜项目文档中心

这里集中记录 ModelMirror 的产品叙事、当前实现、目标架构、模块契约和历史决策。阅读时应先区分“今天怎样运行”和“未来希望怎样设计”。

最后更新日期：2026-08-09
维护人：模镜团队

## 文档状态规则

| 状态 | 含义 | 能否作为当前实现依据 |
| --- | --- | --- |
| 当前 | 已按当前代码、路由和测试校准 | 可以 |
| 冻结 | 描述已完成能力基线，只接受兼容维护 | 可以，但新增路线需另立方案 |
| 目标/研究 | 描述目标架构、战略方向或长期研究边界 | 不可以，必须结合成熟度矩阵 |
| 历史/归档 | 保留决策、失败复盘或旧集成方法 | 不可以 |

遇到冲突时，以代码和测试为最高事实，其次为
[REPOSITORY_FACTS.md](./REPOSITORY_FACTS.md) 与“当前”文档。历史文档中的
“稳定”“主路径”“下一步”只代表当时背景。

## 首次了解 ModelMirror

| 文档 | 状态 | 回答的问题 |
| --- | --- | --- |
| [根 README](../README.md) | 当前 + 方向概览 | ModelMirror 是什么、今天能做什么、目标产品引擎是什么？ |
| [VISION.md](./VISION.md) | 战略/目标 | 为什么需要 AI Capability Compiler，品牌、生态与商业方向是什么？ |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 当前 | 当前主分支真实运行的路由、数据流、存储和外部依赖是什么？ |
| [AI Capability Compiler 目标架构](./architecture/ai-capability-compiler.md) | 目标/研究 | 八层架构、Capability IR、Router Federation、反馈回路和成熟度如何设计？ |
| [GLOSSARY.md](./GLOSSARY.md) | 当前 + 目标术语 | 当前产品词汇与目标架构术语怎样统一？ |

`VISION.md` 解释“为什么与去哪里”，`ARCHITECTURE.md` 记录“今天怎样运行”，目标架构文档说明“未来系统如何分层”。三者职责不同，不能互相替代。

## 当前入口文档

| 文档 | 状态 | 简介 |
| --- | --- | --- |
| [REPOSITORY_FACTS.md](./REPOSITORY_FACTS.md) | 当前 | 可由仓库证明的事实、稳定入口和已知债务。 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 当前 | 当前原生架构、路由、存储和外部依赖。 |
| [QUICK_START.md](./QUICK_START.md) | 当前 | Docker 与本地热更新启动。 |
| [ONBOARDING.md](./ONBOARDING.md) | 当前 | 新成员事实优先级、模块导航与 Harness 流程。 |
| [FRONTEND.md](./FRONTEND.md) | 当前 | React 路由、自适应工作区和 SSE UI 约束。 |
| [BACKEND.md](./BACKEND.md) | 当前 | FastAPI 包边界、API 分组和环境变量。 |
| [DATABASE.md](./DATABASE.md) | 当前 | SQLite、Chroma/FTS5、DuckDB 与文件型 Store。 |
| [DEPLOYMENT.md](./DEPLOYMENT.md) | 当前 | Compose 服务、可选 profile、备份与回退。 |
| [HARNESS_ENGINEERING.md](./HARNESS_ENGINEERING.md) | 当前 | 开发护栏、验收和高风险变更规则。 |
| [GLOSSARY.md](./GLOSSARY.md) | 当前 | 用户术语与内部兼容名。 |

## 当前模块文档

| 文档 | 状态 | 简介 |
| --- | --- | --- |
| [MODEL_ROUTER_NATIVE.md](./MODEL_ROUTER_NATIVE.md) | 当前/冻结阶段 | 原生调度阶段 0–4、灰度门禁和侧车回退。 |
| [OMNIROUTE_INTEGRATION.md](./OMNIROUTE_INTEGRATION.md) | 当前兼容层 | 固定侧车、诊断、供应链与经验教训。 |
| [MULTIMODAL_FORMAT_AUDIT.md](./MULTIMODAL_FORMAT_AUDIT.md) | 当前 | 模态/格式矩阵、STT、TTS 和视频闭环。 |
| [MODEL_CATALOG_MAINTENANCE.md](./MODEL_CATALOG_MAINTENANCE.md) | 当前 | 静态目录快照更新和回退。 |
| [RAG_INTEGRATION.md](./RAG_INTEGRATION.md) | 当前 | 本地 RAG、知识流水线、检索与引用。 |
| [MCP_INTEGRATION.md](./MCP_INTEGRATION.md) | 当前 | MCP stdio、安全边界和测试。 |
| [MCP_CATALOG_ROADMAP.md](./MCP_CATALOG_ROADMAP.md) | 规划 | MCP 中文目录边界、安全适配、自定义连接与 Builder 远期路线。 |
| [SKILL_INTEGRATION.md](./SKILL_INTEGRATION.md) | 当前 | Skill 安装、注入和供应链边界。 |
| [SKILL_EXPERIENCE_AUDIT.md](./SKILL_EXPERIENCE_AUDIT.md) | 当前 | Skill 目录治理、Creator V1 质量门与后续资源化增强边界。 |
| [META_AGENT.md](./META_AGENT.md) | 当前 | Meta Planner 当前契约。 |
| [BENCHMARKS.md](./BENCHMARKS.md) | 当前 | 标准 Benchmark 目录、数据来源、实例化和后续生成边界。 |
| [workflow-native-design.md](./workflow-native-design.md) | 当前设计记录 | classic/shared 能力增量和 native 实验边界。 |

## 冻结与内部兼容文档

`Xpert` 是历史内部契约名，用户界面统一使用“智能体”“Agent Studio”和
“Agent App”。下列文档保留内部类名、API 和 Store 名，不能据此恢复旧 UI 文案。

- [XPERT_FREEZE.md](./XPERT_FREEZE.md)：冻结后的唯一状态入口。
- [XPERT_ALIGNMENT.md](./XPERT_ALIGNMENT.md)：历史增量总记录。
- `XPERT_*.md`：各已实现领域契约与兼容边界。
- `EVOAGENTX_*.md`：已审计但未整体复制上游代码的规划、评测和进化记录。

## 历史与归档

| 文档 | 状态 | 阅读目的 |
| --- | --- | --- |
| [INTEGRATION_DIFY.md](./INTEGRATION_DIFY.md) | 历史/归档 | 旧 Dify iframe 与代理方案；不是当前部署指南。 |
| [postmortem-workflow-rewrite.md](./postmortem-workflow-rewrite.md) | 历史复盘 | 记录一次失败重写及 Harness 教训。 |
| [retry-plan-workflow-native.md](./retry-plan-workflow-native.md) | 已完成/被后续实现取代 | 早期恢复路线，不是未来排期。 |

## 推荐阅读路径

- 首次访问者：根 README → Vision → Architecture。
- 新成员：Facts → Quick Start → Architecture → Harness。
- 生态伙伴：Vision → AI Capability Compiler 目标架构 → Glossary。
- 前端：Frontend → Multimodal → 相关页面模块文档。
- 后端：Backend → Database → 相关领域模块文档。
- 运维：Deployment → Database → Model Router / OmniRoute。
- AI Agent：根 `AGENTS.md` → Facts → Harness → 任务对应模块。

## 文档维护要求

- 写状态，不写愿望；计划必须明确标为计划。
- 入口、组件和 API 名称先用 `rg` 对真实代码核验。
- 当前文档不得把可选或 legacy 集成写成系统前提。
- 历史文档保留原始事实，但必须有醒目的归档头和当前结论。
- 用户术语与内部兼容标识分开记录。
- 修改后运行 Markdown 相对链接检查、陈旧术语扫描和 `git diff --check`。
