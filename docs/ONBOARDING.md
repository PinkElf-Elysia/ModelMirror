# 新成员入职指引

最后更新日期：2026-07-28
维护人：模镜团队

## 第一天先建立事实基线

1. 阅读 [REPOSITORY_FACTS.md](./REPOSITORY_FACTS.md)，区分当前事实、冻结基线和历史方案。
2. 按 [QUICK_START.md](./QUICK_START.md) 启动项目。
3. 打开 `/models`、`/chat/auto`、`/agents`、`/workflow`、`/rag` 和 `/studio`。
4. 阅读 [ARCHITECTURE.md](./ARCHITECTURE.md) 与
   [HARNESS_ENGINEERING.md](./HARNESS_ENGINEERING.md)。
5. 根据任务阅读模块文档；不要从历史复盘直接推断当前实现。

当前 `/workflow` 是 classic 自研画布，`/rag` 是本地知识系统。Dify 文档已归档，
不是本地环境前提。

## 开发环境

必备：

- Node.js 22。
- Python 3.11+。
- Git。

推荐：

- Docker Desktop / Docker Compose。
- 独立的 newAPI 或 OpenRouter 测试 Key。

验证：

```bash
cd client
npm.cmd run build
```

```bash
python -m py_compile server/main.py
python -m pytest server/tests/ -q
```

## 模块导航

| 领域 | 代码入口 | 文档 |
| --- | --- | --- |
| 前端与路由 | `client/src/App.tsx`、`client/src/pages/` | [FRONTEND.md](./FRONTEND.md) |
| 后端装配 | `server/main.py`、`server/api/` | [BACKEND.md](./BACKEND.md) |
| 智能调度 | `server/model_router/`、`server/context_engine/` | [MODEL_ROUTER_NATIVE.md](./MODEL_ROUTER_NATIVE.md) |
| 多模态 | `server/multimodal/`、自适应工作区组件 | [MULTIMODAL_FORMAT_AUDIT.md](./MULTIMODAL_FORMAT_AUDIT.md) |
| 工作流 | `client/src/components/workflow/`、`server/workflow_native/` | [workflow-native-design.md](./workflow-native-design.md) |
| RAG | `server/rag/`、`client/src/pages/Knowledge*` | [RAG_INTEGRATION.md](./RAG_INTEGRATION.md) |
| Agent | `server/xperts/`、`server/xpert_runtime/` | [XPERT_FREEZE.md](./XPERT_FREEZE.md) |
| 工具与扩展 | `server/mcp/`、`server/toolsets/`、`server/skills/` | MCP、Toolset、Skill 文档 |

`Xpert*` 是历史内部契约名。用户界面使用“智能体”“Agent Studio”“Agent App”；
不要仅为改名迁移内部 API、Store 或类型。

## Harness 工作方式

每个任务都应包含：

- Inspect：先读取真实路由、接口和测试。
- Plan：写明范围、验收和回退。
- Implement：一次只解决一个可验证问题。
- Verify：运行最小必要检查，高风险主路径运行全量回归。
- Document：同步当前状态；历史决策使用归档标识。
- Commit：人工验收后再提交。

禁止：

- 提交 `.env`、密钥、日志或持久化业务数据。
- 用实验路径替换 `/workflow`、`/rag` 或 `/api/chat` 而没有门禁。
- 将“计划”“占位”写成当前可用能力。
- 用旧 Dify 或 Xpert 对齐文档覆盖当前仓库事实。
- 使用不安全批量替换写入中文源码。

## PR 最低信息

- 改动目标与非目标。
- 修改文件和公共契约。
- 自动验证与人工验收。
- 已知风险、功能开关和回退方法。
- 是否涉及外部费用、隐私、凭据或持久化迁移。

分支默认使用 `codex/<short-description>`；提交信息遵循
`type: 简短中文说明`。

## 文档状态

- “当前”：可作为实现和运维依据。
- “冻结”：描述已完成基线，只接受兼容维护。
- “历史/归档”：保留决策背景，不可作为当前部署或入口依据。

遇到冲突时，优先级为：真实代码与测试 → `REPOSITORY_FACTS.md` →
当前模块文档 → 冻结文档 → 历史文档。
