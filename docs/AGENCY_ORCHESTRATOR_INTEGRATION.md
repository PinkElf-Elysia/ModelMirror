# Agency Orchestrator 专家团集成

模镜选择性迁入 Apache-2.0 的
[`agency-orchestrator`](https://github.com/jnMetaCode/agency-orchestrator)，固定上游提交为
`3b7c43042325a9091393de6ecfa7e9936b0c7932`。第三方源码、Blob SHA、许可证和本地映射见
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

## 验收

```powershell
npm.cmd run test:all --prefix server/orchestration_worker
python -m pytest server/tests/test_expert_team_agency_planner.py -q
python -m pytest server/tests/test_meta_agent.py -q
python -m pytest server/tests/test_agent_strategies.py -q
npm.cmd run build --prefix client
```

自动测试只使用 Fake Gateway，不产生真实模型费用。真实网关 smoke 需在已有测试凭据且明确接受费用时单独执行。
