# Agency Orchestrator 专家团集成

模镜选择性迁入 Apache-2.0 的
[`agency-orchestrator`](https://github.com/jnMetaCode/agency-orchestrator)，固定上游提交为
`e3f69fdf9da8a4630edbb8abeb116893b983b57d`。第三方源码、Blob SHA、许可证和本地映射见
`server/vendor/agency-orchestrator/`。

## 当前边界

- 产品入口仅位于 `/expert-team` 的“自动路由派工”内；`/agents/meta-agent` 及其导航不增加入口。
- Python 服务持有网关密钥，并通过一次性 Node 子进程调用迁入的 Compose 核心；Worker 不持有 Provider 密钥。
- Agency 输出复用 `MetaPlannerTaskPlan`、`MetaPlannerBlueprint`、Xpert/Workflow candidate 和现有工作流校验器。
- 预览不会创建 Authoring Proposal、发布 Xpert、自动启动 AI Team 或执行 DAG。
- 应用预览只填充专家团现有 AI Team 的阵容、个人任务和团队目标；实际执行仍使用原接力/辩论接口。

## 配置与接口

`EXPERT_TEAM_AGENCY_PLANNER_ENABLED=0` 默认关闭。关闭时：

- `GET /api/expert-team/planner-capabilities` 仍返回能力和固定上游版本；
- `POST /api/expert-team/plan-preview` 返回明确的 disabled 错误，不调用模型。

启用后，规划接口支持从全部当前专家自动选择，或固定使用最多 6 位现有 AI Team 专家。只有用户显式点击“生成智能组队预览”时才会调用规划模型。一次 Worker 请求最多 3 次模型调用，具体费用由所选模型和网关计费决定。

编辑预览生成的任务、依赖或验收标准后，前端继续调用现有
`POST /api/workflow-native/validate` 重新校验，不维护第二套验证协议。

## 观测与回退

规划运行写入现有 RunRegistry，类型为 `meta_planner`，并记录
`surface=expert_team`、`backend=agency_orchestrator` 和固定上游提交。

紧急回退只需将 `EXPERT_TEAM_AGENCY_PLANNER_ENABLED` 设为 `0` 并重启服务。原快速单专家派工、`/api/route-agent`、AI Team 和 `/api/team/chat` 不依赖该开关，仍保持可用。

## R3 受控 DAG 执行 Beta

`EXPERT_TEAM_AGENCY_EXECUTION_ENABLED=0` 独立控制 AI Team 的 DAG Beta，默认关闭；规划预览开关可单独开启。启用后，服务端仍会重新核对能力快照、上游版本、专家目录、计划与工作流一致性，再通过 `mm-agency-bridge/v2` 调用上游 `executeDAG`。

执行面只接受 1–6 个普通文本模型步骤，最多并发 2 个模型请求、最多 10 次逻辑模型调用、单次最多 4096 输出 token，单次请求超时 180 秒、整体超时 900 秒。仅最终汇点执行验收核验和最多一次返工。Skill、工具、审批、人类输入、条件、循环、步骤级 Provider/密钥/模型覆盖均会被拒绝。

后台事件复用 `WorkflowExecutionStore`，支持 SSE 断线后按 `after_sequence` 重放和幂等取消；不支持跨服务重启续跑，重启后未完成任务会标记为 `agency_execution_interrupted`。紧急回退只需将 `EXPERT_TEAM_AGENCY_EXECUTION_ENABLED=0` 并重启服务；规划预览、快速派工、串行接力、独立辩论和元智能体入口不受影响。

## R3.5 工作台闭环

智能组队可选择现有 RAG 资料库作为规划参考，复用 `/rag` 的上传、解析、索引和检索链路，不创建第二份文件资产或角色目录。资料库内容默认不用于专家团规划；用户每次选择资料库后，必须显式勾选授权，服务端才会在点击“生成智能组队预览”时按现有 RAG 检索配置处理查询与候选片段，检索最多 4 个片段，并把最多 12,000 字符的命中原文发送给当前规划模型及其配置网关。返回前端的来源清单只含文档、位置和评分元数据，不回显片段原文。未授权、资料库不存在或检索失败时不会静默降级为外发。

专家团页面通过 `GET /api/expert-team/dag-runs` 读取服务端最近运行，不再只依赖浏览器 `localStorage` 发现历史任务。列表只返回运行摘要，不包含完整事件或专家提示词；打开具体任务后仍复用原状态与 SSE 接口。

完成的 DAG 结果可直接复制或下载为 Markdown。该下载在浏览器本地生成，不增加模型调用，也不改变现有文件输出、RAG 或经典工作流链路。关闭执行开关仍会阻止新任务启动，但不会隐藏已经持久化的历史结果。

## 验收

```powershell
npm.cmd run test:all --prefix server/orchestration_worker
python -m pytest server/tests/test_expert_team_agency_planner.py -q
python -m pytest server/tests/test_meta_agent.py -q
python -m pytest server/tests/test_agent_strategies.py -q
npm.cmd run build --prefix client
```

自动测试只使用 Fake Gateway，不产生真实模型费用。真实网关 smoke 需在已有测试凭据且明确接受费用时单独执行。
