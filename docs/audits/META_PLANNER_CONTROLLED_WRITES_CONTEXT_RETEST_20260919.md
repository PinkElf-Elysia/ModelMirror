# 受控写入修复上下文真实复测

日期：2026-09-19。结论：真实生成失败，未达到本轮验收或 PR 提交门禁。本次没有修改生产代码。

## 冻结范围

- 工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout`；分支 `codex/meta-planner-controlled-writes-10-closeout`；HEAD `a7d99925584e818e7f2df84b1646256abfc08ed6` 加既有未提交修改。
- 本次授权一次预览器生成，最多三次 completion；OpenRouter / `deepseek/deepseek-v4-flash-0731`，温度 `0.2`，Agent 上限 `2`。
- 目标与上次完全一致：按批次查询合成质检表，缺失安全终止，低分只更新 status，其他分支不写入，分别由 Agent 解释真实证据。
- 表为 `table_7565c77441414604b64c01739977f1d5`，查询授权与 `update/status/1 行` 写授权均保持原值。不新增其他资源、工具或中间件。
- 请求规范化 checksum 为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`，与上次真实请求相同。
- 已核对 101 个源码/构建文件的哈希及 6 份历史调用账本。本次只在独立预览 `15459 -> 16459` 重载后端，没有操作共享栈。
- 新账本从零开始，一次生成被领取后不能重新领取；禁止评测和未授权外发。视觉目录的自动外部查询被护栏阻止，视觉保持未授权。

## 实际结果

通过可见页面填写目标、模型、节点与资源范围后，只点击一次“生成候选智能体”。

| 阶段 | Provider 结果 | Provider 报告 Token | 工程结果 |
| --- | --- | ---: | --- |
| 任务规划 | HTTP 200 / stop | 3,105 | 通过 |
| 能力编译 | HTTP 200 / stop | 16,324 | 六项诊断，未完成解析与编译 |
| 唯一 Patch 修复 | HTTP 200 / stop | 17,561 | Patch 可解析，整批应用校验失败 |

合计三次 completion、36,990 Token；没有不确定派发，没有自动重试。Token 为 Provider 回报，不是账单审计。

结果 Proposal：`proposal_6087ff47a9e9426687261ec5d0b1d838`，`pending`、revision `1`。`candidate_origin=server_synthesized_fallback`，`graph_ir_status=fallback_unapprovable`。页面明确显示诊断占位图、不可批准。

## 可证实的失败边界

1. 首次能力编译包含两项 JSON 节点输入约束错误、一项控制流错误、两项 `DATA_UNKNOWN_VARIABLE` 和一项 `DATA_UNREACHABLE`。
2. 模型返回十项 Patch 操作，操作数与结构 checksum 在规范化前后不变，没有发现修复操作被规范化删除。
3. `serialize_evidence` 修复前接入两个 `value` 输入。对它执行的 `update_node` 没有改变配置 checksum，输入数量仍为二；没有相应的解除绑定操作。
4. 原子 Patch 最终校验拒绝：`Node serialize_evidence requires exactly one value input.`
5. 部分 Query/Update 的端口数量在 Patch 中恢复为与当前配置一致，但这不是完整类型或可达性验证。`output_normalization`、重新解析、授权、resolve、compile 和 publish preflight 均被 `patch_apply` 阻断。
6. 因此，六项初始诊断变成一项首个失败信息，不表示其余五项已消除。fallback 的缺少 Agent 模型告警也不能被误当作模型候选新增根因。
7. 三阶段的请求契约投影符合预期；Provider 到 collector 的正文 checksum、collector 到 validator 的结构 checksum 一致，没有发现中间采集层篡改模型结果的证据。

本样本证伪了“加入关联清单后，唯一修复已经足以可靠完成该分支案例”的判断。但单次样本不能区分模型能力上限、提示注意力分配和完整修复策略的贡献，也不能据此提前引入多 Agent 或放宽校验。

## 保持的安全边界与未验证项

- 合成活表前后均为零记录、零操作账本条目。
- 后端仅记录一次生成 POST；没有评测、Workflow 执行、Proposal 批准、Xpert 草稿写入或发布。
- 没有提交、推送或创建 PR。本次临时启动器、调用账本和证据均在忽略目录 `.tmp-cw10-context-retest-20260919/`。
- `verify.py` 核对通过，生成 `retest-proof.json`；它不预设候选成功，也不修改生成结果。
- 前批 1218 项受影响回归、语法与前端构建结果没有被本次重跑替代。全量仍停留在 94 通过、1 失败后停止的未完成门禁，见 [修复记录](META_PLANNER_CONTROLLED_WRITES_REPAIR_CONTEXT_20260919.md)。
- 原始完整模型图仍未作为报告正文留存；本记录只使用已持久化的安全诊断、Patch 变更摘要和 checksum，不宣称可逐字重放全部模型输出。
- 下一步应先离线核对纯节点输入基数是否在修复关联清单中与动态资源同等完整呈现，以及唯一修复是否覆盖全部独立错误。不能仅补本次节点名或把删掉一条输入当作完整方案。后续真实调用需要独立授权。
