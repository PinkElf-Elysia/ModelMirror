# CW10 真实复测失败与生成链路调整边界

## 结论

2026-09-20，在上次权威资源类型修复后，通过独立预览器执行一次同请求真实生成。
**仍未通过，整轮 PR 门禁未达到。** 按用户本次指示，停止追加调用和局部补丁循环。
本次没有修改生产代码；本文是失败取证与下一阶段设计审查依据，不是新的实现计划或验收通过声明。

当前证据支持优先调整生成与修复链路的职责分配，不支持认定 DeepSeek 已到能力上限、
单模型不可行，或必须提前引入 V4 多 Agent。也没有证据要求重做 SQLite、写入事务或 Runner。

## 固定范围与真实结果

- 工作树：`C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`，HEAD `a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 开始时已有 140 项修改/未跟踪路径，暂存为空；主工作区与共享栈不参与。
- 预览入口：`http://127.0.0.1:15459/agents/meta-agent`；只点击一次生成。
- Provider / 模型：OpenRouter / `deepseek/deepseek-v4-flash-0731`。
- temperature `0.2`、max_agents `2`、最多三次 completion；不执行 Evaluation 或工作流。
- 同一零记录合成质检表，授权 Query 和 `update/status/1`，其他资源不授权。
- 目标仍为不存在记录时安全终止、低分更新、高分不写入的互斥分支；没有改成串行目标。
- 原请求 checksum：`eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`。
- Run：`488706e0-b1b2-404d-a333-5f5b63be813d`。
- Proposal：`proposal_a703150779c44e11a2cc13f8098405fe`，pending / r1，`validation.valid=false`。

| 调用 | 输入 Token | 输出 Token | 合计 | 结果 |
| --- | ---: | ---: | ---: | --- |
| task_plan | 2,407 | 646 | 3,053 | 完整返回；任务计划通过 |
| capability_compile | 13,213 | 2,232 | 15,445 | 完整返回；控制流校验失败 |
| graph_patch_v1 | 16,413 | 528 | 16,941 | 完整返回；Patch 应用后相同控制流错误仍存在 |

三次均为 HTTP 200、正常完成；共 35,439 Provider 上报 Token，0 次不确定派发。
这是 usage 回执，不是账单审计。Run 的 completed 仅表示生成程序结束，不代表候选有效。

119 个冻结文件及 11 份历史调用账本复核不变。该合成活表前后均为 0 记录、0 操作；
没有执行写节点、评测、审批、发布、Commit、Push 或 PR。
仅重启本任务独立后端加载已完成修复及本次受限预算，前端与共享栈未重建。

## 本次可以精确确认的失败链

1. 任务计划首次通过；上一轮 `input_contract` 非列表错误没有再次出现。
2. Graph Intent 解析通过，在 authorization 内部的控制流分析失败；resolve、compile、
   publish_preflight 均被阻断，不能把未执行阶段称为通过。
3. 真实来源已解析为 Query 的 nullable object；`check_score` 的字段是必填 integer，
   字段本身不是 nullable。验证器报告某个到达字段比较的路径没有有效对象保护。
4. 唯一 Patch 的解析、归一化、应用均完成，随后仍在同一阶段失败。

核心错误为：

```text
Router check_score has unproven outcomes:
可达输入触发 CONDITION_FIELD_REQUIRES_OBJECT，缺少有效的路径保护。
Scenario 1 reaches 0 terminals (success=[], error=[]).
Router check_score has shadowed or unproven outcomes: matched, unmatched
Control flow contains unreachable nodes:
explain_agent, serialize_query, serialize_update, update_status
```

控制图 checksum 在首次图和修复后相同：
`d0df1adb706085c065de5ca3978a1eeabe1599a405c10dae7b5ba22064cd72de`。
四个错误指纹也全部相同。

模型提交 9 个 Patch 操作：两次 set_node_resource、四次 update_node、两次 connect_data、
一次 set_final_outputs。没有 connect_control / disconnect_control。
两条 connect_data 是已有边的精确重复，归一化后剩 7 个操作。
`check_score`、两个 Serialize 的配置和输入 checksum 均未变；Agent 配置改变，输入未变。
因此可以确认这次修复没有改变控制拓扑，也没有改变报错比较节点的配置和数据来源。
数据图 checksum 改变包含服务端派生输出归一化，不能把它说成模型已经修好依赖关系。

三次请求的契约 checksum 均符合预期；已观测的 Provider、Collector、Validator
受限结构投影两两一致。没有本次字段在传输中被改坏、超时或输出截断的证据。
投影一致并不等于原始全文逐字证明；`full_payload_replay=false`。

最终展示的是 `server_synthesized_fallback / fallback_unapprovable`。
占位图中的缺模型错误不能倒推为原始生成图根因，也不能手工给占位 Agent 补模型换取通过。

## 尚未证明的边界

- 本次没有保存原始完整失败图。安全证据有节点/输入摘要、控制图 hash、操作类型和变更 hash，
  但不足以逐边重放。不能断言具体是反接了 matched、存在旁路，或某条服务端规则错误。
- 源码表明修复上下文包含原图、路径反例、前驱及必需输入说明；不能说“从未告诉模型”。
  此信息是源码核对，不是对模型注意力或推理过程的测量。
- 共享 Agent 绑定了 Query 和 Update 的序列化结果，是需要后续路径证明的风险；原图未完整
  保留，不能直接把它宣称为本次四条错误的唯一原因。
- 新资源类型处理未完整进入 resolve / compile；不能仅凭本次未出现该错误就宣布全部修好。
- 只有一个本次样本，不估计成功率，不与不同目标的历史线性成功混算稳定性。

## 为什么不再继续局部补丁

此前已实施严格生成 Schema、配置相关端口投影、权威资源类型、同来源联合路径分析、
结构化路径反例、直接前驱说明、完整原图修复和一次修复预算护栏。
参见[系统收口](META_PLANNER_CONTROLLED_WRITES_SYSTEM_CLOSEOUT.md)、
[固定契约核对](META_PLANNER_FIXED_CONTRACT_AUDIT_20260920.md)及
[上次修复](META_PLANNER_RESOURCE_TYPE_REPAIR_20260920.md)。

这些措施能解释局部接受/拒绝，却没有证明真实模型能在同一预算内可靠完成组合任务。
本次修复输入已有 16,413 Token，却返回未改变失败控制关系的 Patch。
这足以否定“再补一段空值说明即可交付”的结论；不能仅凭长度认定注意力不足是唯一根因。

源码可确认的系统风险是：模型仍需同时生成业务节点、数据引用、重复变量/类型声明、
任意平面控制边和最终来源；修复又要维护同一图的多个相关表示。
第一阶段主要分解 Agent 职责，不产生可机读的业务分支义务。
这种模型与编译器分工是否过重，需要以协议级对照验证，不能继续靠扩大文字约束判断。

## 推荐的调整方向，尚未实施

| 层 | 下一阶段应审查的变化 | 必须保留的边界 |
| --- | --- | --- |
| 模型生成协议 | 评估仅用于生成的受限结构化顺序/分支表示，再确定性展开到既有 GraphIntent V3；模型仍负责显式选择业务条件及分支，不让服务端猜测接边 | 不更换公共 IR、Runner、SSE；不是新增表达式引擎；不能用串行模板替代真实分支 |
| 机械事实分配 | 可由 NodeContract、Adapter、资源快照唯一推出的变量、端口类型和版本由同一路径派生；减少模型在多处重复抄写同一事实 | 来源、权限、Schema、revision、结果类型和效果检查不放宽；含糊关系明确拒绝 |
| 验证与唯一修复 | 从已解析同一图生成最小因果反例与必要上下文；保留完整安全约束，但让修复对象针对失败的分支义务，而非重发无关资源/配置 | 一次修复、总三次 completion；最终仍重跑整图门禁；没有因果进展就失败，不自动接边或再调用 |
| 泛化证据 | 先固定跨目标矩阵和负对照，再做新旧协议配对；分别记录首次合格、修复合格、执行/效果合格及成本 | 不以人工合法图、总绿测数或一次模型成功代替真实泛化 |
| 可重放取证 | 为合成验收设计可重放的最小结构见证，保留同来源关联、分支与依赖；未知内容脱敏后明确有损边界 | 不落盘完整 Prompt、隐藏推理、凭据、业务记录；不能把有损投影伪称原图重放 |

上述是优先评估的架构方向，不宣称已证明其优于当前协议。不要另建“第四套权威类型”，
不要一次性重写所有模块，也不要把更多提示、更多 normalizer 或更多 paid retry 命名为系统优化。

本轮维持 22 类节点、原写授权、可信记录、单节点事务、隔离效果评测及审批只写草稿。
第 8 轮不启动。若设计需要正式 Requirement IR、模式库、Planner/Binder/Reviewer 等
已锁定第 9 轮能力，须单列依赖调整审计并由用户确认，不悄悄提前；V4 多 Agent 仍延期。

建议设计验收至少覆盖空/有记录、条件正反方向、互补分支、旁路、分支局部输入、只读和写入、
两个表结构及不同中文需求表达。错误修复必须证明原不变量恢复且无新越权；成功后才进入
另行授权的真实配对试验。停止标准和调用预算应在外发前冻结，不边试边改目标。

## 本次取证与剩余门禁

本次只执行真实 UI 生成、只读证据核对及本文记录，不重复运行未变代码的离线全量。
上一修复批的 1194 项集成与 169 项运行/评测检查通过属于离线证据，不能替代本次失败。
最新全量入口仍为 94 passed / 1 failed / 其余未运行，阻塞是独立 Worker 断连测试；
没有声称其在最新干净 main 已复现，更没有忽略该测试或修复它。

真实调用后的只读复核命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B `
  .tmp-cw10-resource-types-retest-20260920/verify.py `
  488706e0-b1b2-404d-a333-5f5b63be813d
```

该命令通过表示证据与授权范围一致，**不表示生成通过**。
本次忽略目录 `.tmp-cw10-resource-types-retest-20260920/` 保留而不纳入 Git：

| 文件 | SHA-256 |
| --- | --- |
| retest-proof.json | `cb71841dfa141ba9d24799aaf26e696497f91af4f494cafbe4565063adb67be1` |
| calls.json | `449dc680592502b25fad8207217debbc2ecd92f4b70df7784526a8665255e3ed` |
| source-receipt.json | `285922a628f7428f13d4ffc1b06b7044035b4eca149c4541f06b4a3c7ad254db` |
| launch.py | `81964eab4d3b4aa3c7879d37b0baa00149293dff508da1cab5214036767a83eb` |
| verify.py | `174f569af2e221a0e31099cca3c4c2a443f4c25650b7de8e5cc0b40da0d50e3a` |

本次无运行数据迁移或代码回退需求。保留失败 Proposal 与耗尽的授权账本，不能删除重置以
追加调用。后续设计先在独立离线范围验证，再单独确认实施和真实实测门禁，不提交当前轮次 PR。
