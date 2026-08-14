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
- 应用预览会填充专家团现有 AI Team 的阵容、个人任务和团队目标；串行/辩论继续使用原接口，启用独立执行开关后也可由用户确认启动受控 DAG Beta。

## 配置与接口

`EXPERT_TEAM_AGENCY_PLANNER_ENABLED=0` 默认关闭。关闭时：

- `GET /api/expert-team/planner-capabilities` 仍返回能力和固定上游版本；
- `POST /api/expert-team/plan-preview` 返回明确的 disabled 错误，不调用模型。

启用后，规划接口支持从全部当前专家自动选择，或固定使用最多 6 位现有 AI Team 专家。只有用户显式点击“生成智能组队预览”时才会调用规划模型。一次 Worker 请求最多 3 次模型调用，具体费用由所选模型和网关计费决定。

尚未启动的组队预览和已载入 AI Team 的 DAG 草稿会在当前浏览器标签页中保留最多 24 小时，页面刷新或后端预览服务重启后可恢复，不会因此再次调用规划模型。该草稿使用会话级浏览器存储，不跨标签页或设备同步；服务端启动 DAG 时仍会重新核对能力快照、上游版本和完整计划契约。编辑任务或依赖时，前端通过编译器写入的 `plannerTaskIds` 绑定真实节点 ID，不再假定旧式 `agent_<task>` 节点名。

编辑预览生成的任务、依赖或验收标准后，前端继续调用现有
`POST /api/workflow-native/validate` 重新校验，不维护第二套验证协议。

## 观测与回退

规划运行写入现有 RunRegistry，类型为 `meta_planner`，并记录
`surface=expert_team`、`backend=agency_orchestrator` 和固定上游提交。

紧急回退只需将 `EXPERT_TEAM_AGENCY_PLANNER_ENABLED` 设为 `0` 并重启服务。原快速单专家派工、`/api/route-agent`、AI Team 和 `/api/team/chat` 不依赖该开关，仍保持可用。

## R3 受控 DAG 执行 Beta

`EXPERT_TEAM_AGENCY_EXECUTION_ENABLED=0` 独立控制 AI Team 的 DAG Beta，默认关闭；规划预览开关可单独开启。启用后，服务端仍会重新核对能力快照、上游版本、专家目录、计划与工作流一致性，再通过 `mm-agency-bridge/v2` 调用上游 `executeDAG`。

执行面只接受 1–6 个普通文本模型步骤，最多并发 2 个模型请求、最多 10 次逻辑模型调用、单次最多 4096 输出 token，普通生成单次请求超时 240 秒、整体超时 900 秒。仅最终汇点执行验收核验和最多一次返工。工具型 Skill、工具、审批、人类输入、条件、循环、步骤级 Provider/密钥/模型覆盖均会被拒绝；R5 仅允许服务端白名单中的纯文本“方法 Skill”。

后台事件复用 `WorkflowExecutionStore`，支持 SSE 断线后按 `after_sequence` 重放和幂等取消；不支持跨服务重启续跑，重启后未完成任务会标记为 `agency_execution_interrupted`。紧急回退只需将 `EXPERT_TEAM_AGENCY_EXECUTION_ENABLED=0` 并重启服务；规划预览、快速派工、串行接力、独立辩论和元智能体入口不受影响。

## R3.5 工作台闭环

智能组队可选择现有 RAG 资料库作为规划参考，复用 `/rag` 的上传、解析、索引和检索链路，不创建第二份文件资产或角色目录。资料库内容默认不用于专家团规划；用户每次选择资料库后，必须显式勾选授权，服务端才会在点击“生成智能组队预览”时按现有 RAG 检索配置处理查询与候选片段，检索最多 4 个片段，并把最多 12,000 字符的命中原文发送给当前规划模型及其配置网关。返回前端的来源清单只含文档、位置和评分元数据，不回显片段原文。未授权、资料库不存在或检索失败时不会静默降级为外发。

专家团页面通过 `GET /api/expert-team/dag-runs` 读取服务端最近运行，不再只依赖浏览器 `localStorage` 发现历史任务。列表只返回运行摘要，不包含完整事件或专家提示词；打开具体任务后仍复用原状态与 SSE 接口。

完成的 DAG 结果可直接复制或下载为 Markdown。该下载在浏览器本地生成，不增加模型调用，也不改变现有文件输出、RAG 或经典工作流链路。关闭执行开关仍会阻止新任务启动，但不会隐藏已经持久化的历史结果。

## R5 可复用资产与方法 Skill

专家团的任务模板和固定阵容复用上游 Prompt Lab 与 Team/Loadout 文件格式，通过同一个一次性 Node Worker 读写。默认存储目录为现有 `AGENT_TASK_STORAGE_DIR` 下的 `expert_team_assets/`，因此 Docker 部署会随既有 `server/xpert_runtime/storage` 卷持久化；本机直跑服务时可用 `EXPERT_TEAM_AGENCY_ASSET_DIR` 指定独立目录。浏览器旧版 `localStorage` 阵容只做兼容读取，新保存内容以服务端为准。同名任务模板保存会追加上游 Prompt 版本历史；R5 不提供删除接口。

方法 Skill 只从模镜内置 Skill 库中选择当前可注入且显式允许的 `data-analysis`、`software-engineering`、`web-design`。规划结果只保存 Skill ID，DAG 启动时浏览器提交当前摘要，服务端重新读取正文并核对摘要后才传给 Worker；客户端不能提交任意 Skill 正文。Worker 只把方法正文注入对应步骤的 system prompt，不开放文件、命令、MCP、Provider 或其他外部副作用能力。

若可复用资产出现问题，可停止使用模板/阵容而不影响规划与执行；设置 `EXPERT_TEAM_AGENCY_EXECUTION_ENABLED=0` 可立即阻止新的方法 Skill DAG 执行。资产目录可以整体备份，回退 R5 代码不会改变其中采用的上游 Team/Prompt 可读格式。

## R5.1 可靠性修复（与 R5 合并验收）

R5.1 不是独立交付轮次，而是 R5 真实用户实例未通过后增加的修复轮。R5 的服务端可复用资产、固定阵容、任务模板版本、方法 Skill、专家团 UI 与 DAG 执行体验，必须连同下述可靠性修复重新做完整人工验收；不能仅凭 R5.1 的定向复测结果判定 R5 或 R5.1 通过。两轮只有在整体验收通过后才能一起进入提交和 PR 阶段。

自动阵容超过 `max_agents` 时会占用原有“最多一次最终修复”机会，要求模型在允许的专家集合内缩减阵容；不会新增模型修复轮次，也不会在服务端直接裁剪可能仍被任务引用的专家。规划响应同时返回实际调用次数和 Token 用量，并在页面展示“输入未明确的事实必须标记为待确认”的假设约束。

网关会保留上游 `finish_reason`；若输出达到 token 上限，规划或执行均以 `model_output_truncated` 失败，不再把截断文本当作完整结果。为容纳 DeepSeek 在真实多模块规划中的结构化 YAML 与推理开销，Compose 规划输出预算为 10240 token，并要求任务只携带本步骤相关事实、避免逐步重复整个目标；DAG 执行的单次 4096 token、最多 10 次调用护栏保持不变。规划阶段截断会明确提示尚未生成可执行计划，不再错误提示重试不存在的失败步骤。空响应、不可解析响应、网关超时和网关失败也使用可操作且不泄露上游响应正文的稳定错误类别。失败执行仍持久化已产生的累计调用和 Token，因此刷新页面不会把运行中或失败用量归零。

失败任务若已有可复用的完成步骤且累计调用少于 10 次，可在再次确认可能产生费用后创建新的续跑任务。服务端冻结原工作流，重新核对上游版本、能力快照、专家和方法 Skill 摘要，再把已完成输出交给上游 Executor 的 `skipStepIds` / `restoredStepMeta`；已完成步骤不会再次调用模型，失败及其下游步骤才会执行。原失败任务保持不变，新任务记录 `resumed_from_task_id`，总调用次数和 Token 用量沿续跑链累计。该能力不是跨服务进程 Resume：服务重启时仍不会恢复正在运行的 Node 进程。

执行连接器会在不改变单次 4096 token 硬上限的前提下，要求普通步骤输出完整、紧凑的交付物，并禁止编造输入和依赖结果中不存在的人名、日期、预算、指标、供应商或基础设施；缺失事实必须使用负责人角色和 `TBD` / “待确认”。最终验收调用通过 `mm-agency-bridge/v2` 的可选 `json_response` 标记请求严格 JSON，Python 宿主复用现有 reasoning JSON 恢复链路，兼容把结构化裁决放在 reasoning 字段而正文为空的文本模型。普通生成请求仍使用原文本响应契约。

最终汇点渲染时会把过长的直接依赖限制为总计 15,000 字符的高信号摘录，完整步骤产物仍保存在运行历史和失败续跑档案中；该处理不增加模型调用。若最终验收标准显式声明字符数上限，Worker 会预留系统事实边界所占字符后做确定性计数，超限时直接复用上游 Executor 的一次返工逻辑，再进入语义核验。最终步骤同时把原始用户请求置于核验任务最前方作为唯一权威事实源，避免验收员把真实用户输入误判为上游编造内容；JSON 验收员必须为失败项引用精确冲突或缺失证据，不得把相关表格、标题或列表中已出现的字面标记误报为缺失。

2026-08-12 使用已有授权额度完成 DeepSeek 定向修复复测：两步 DAG 加一次最终核验共 3 次执行调用，最终 `quality_status=passed`，无返工、无警告、无截断；交付物使用负责人角色并包含 24 处 `TBD` / “待确认”，未复现虚构人名。该阶段结果只证明截断、事实约束和 JSON 核验修复在当前实例有效；后续仍按完整 R5 + R5.1 清单继续验收。

2026-08-13 使用 `deepseek/deepseek-v4-flash-0731` 对自然规模的智能报销预审试点需求复测 Compose：输入 13,233 token、输出 3,742 token，仅 1 次规划调用即生成 5 个任务和 5 位专家，工作流校验通过、无警告、未再触发截断。该证据只解除规划输出上限阻塞；任务模板版本、固定阵容、方法 Skill、DAG 执行、历史恢复和失败续跑仍需作为 R5 + R5.1 整体完成人工验收。

## R6 对话式返工与版本链

`EXPERT_TEAM_AGENCY_REVISION_ENABLED=0` 独立控制对话式返工，默认关闭；规划与 DAG 执行开关保持独立。关闭时，已有 DAG、历史和修订结果仍可查看，`POST /api/expert-team/dag-runs/{task_id}/revise` 返回 `agency_revision_disabled`，页面不显示“要求修改”或“继续完善”，因此不会产生新的模型调用。

返工请求只接受已完成步骤的 `target_task_id` 与 10–4000 字符反馈。服务端从源任务读取冻结的模型、工作流、专家、方法 Skill、上游版本和能力快照，计算目标步骤的全部下游：目标与下游重新执行，其他已完成步骤复用；失败源任务中尚未完成的步骤也会正常补跑。Node Worker 继续复用上游 `executeDAG(..., feedback)` 的“上一版产出 + 用户反馈”提示块，不复制返工算法。`/retry` 仍是失败续跑并沿用剩余累计预算，`/revise` 是用户主动修改并获得新的最多 10 次调用预算，两种语义互斥。

每次返工都会创建新的不可变 DAG 任务，原任务、事件和输出不修改。详情接口返回父版本、根版本、修订序号、完整反馈、影响步骤以及本次和版本链累计用量；历史列表只返回 160 字符反馈摘要。相同源任务、目标和反馈的活动请求幂等返回同一任务，另一项活动返工返回 `agency_revision_in_progress`。完整反馈只保存在任务运行元数据，不写入 SSE 事件或公开 RunRegistry；网关密钥仍只存在于 Python 服务进程。

页面提交反馈后会再次显示付费确认，并明确原模型、最多 10 次调用、并发 2 和最长 15 分钟；确认后把新任务写入 `dag_task` URL，并继续复用原状态查询、SSE 重放、取消和刷新恢复。紧急回退只需将 `EXPERT_TEAM_AGENCY_REVISION_ENABLED=0` 并重启服务，无存储迁移，不影响规划预览、DAG 历史、失败续跑、串行接力、独立辩论或元智能体页面。

## 验收

2026-08-13 的 R5 + R5.1 真实 DeepSeek 回归进一步修复了四类用户可见可靠性问题：执行准备阶段只补充任务目标中缺失的模板变量，避免把同一依赖输出重复注入最终汇点；Agency 调用声明低推理预算但继续保持单次 4096 token、累计 10 次调用和 900 秒整体超时。自然规模流程步骤曾在原 180 秒单次限制处超时，因此经用户批准将普通生成调用定向放宽到 240 秒，其余成本护栏不变；JSON 裁决请求使用 `response_format=json_object`，正文中的最终 JSON 优先于 reasoning 中的草稿 JSON，只有正文缺少所需契约时才回退 reasoning；最终交付物由 Worker 确定性附加“事实与决策边界”，明确未由用户输入确认的目标、预算、技术选择和前置条件均为待确认建议，且不得覆盖原始禁止项。该边界同时写入汇点步骤事件和最终输出，不增加模型调用，也不追溯改写旧任务。

真实回归证据包括：`agency_dag_c35ebdc8a20c449c8ce0f81d23e3ea42` 在两次并行 180 秒超时后可安全续跑并复用已完成步骤；`agency_dag_61433b1ec63b4bc993171facbb36660a` 在累计 10 次调用处停止继续计费并如实保留 `quality_status=failed`；单次真实 JSON 裁决正确识别了未标注的建议目标和新增前置条件；修复裁决格式后，`agency_dag_9299a39b8f02421f8aff42215ab34e71` 以 5 个完成步骤、7 次执行调用、`quality_status=passed`、无警告结束。人工抽查仍发现仅依赖同模型裁判可能漏判建议与事实边界，因此新增了上述确定性系统声明。

最终整体验收任务 `agency_dag_49ab33018b474ef4b94c63852f718e74` 复用两个已完成步骤后完成全部 5 个步骤，累计 8 次逻辑调用，`quality_status=passed`、无警告；最终正文 1,172 个 Unicode 字符，满足 1,500 字限制及七段评审包契约。启动校验同步兼容 Agency 原生允许的传递上游变量引用，同时继续拒绝未知变量、非上游变量和缺失的直接依赖输入。2026-08-13 已按模板、固定阵容、方法 Skill、历史恢复、失败续跑、新任务事实边界和原串行/辩论兼容性清单完成人工验收。

```powershell
npm.cmd run test:all --prefix server/orchestration_worker
python -m pytest server/tests/test_agency_worker_bridge.py -q
python -m pytest server/tests/test_expert_team_agency_planner.py -q
python -m pytest server/tests/test_expert_team_agency_execution.py -q
python -m pytest server/tests/test_meta_agent.py -q
python -m pytest server/tests/test_agent_strategies.py -q
npm.cmd run build --prefix client
```

自动测试只使用 Fake Gateway，不产生真实模型费用。真实网关 smoke 需在已有测试凭据且明确接受费用时单独执行。
