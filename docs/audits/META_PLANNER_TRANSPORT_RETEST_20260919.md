# CW10 传输取证真实复测

## 结论

本次真实预览器生成**未通过验收**。三次 completion 均完整返回，未发生本地超时或不确定派发；
唯一一次 Graph Patch 修复后，低分支 Agent 的数据可达性仍不满足契约。
这证明本次已进入语义校验失败阶段，不能将上次响应正文超时直接延用为本次原因。
也不能由一次完整返回推断上游超时已被修复或模型编排已稳定。

## 授权与固定输入

- 用户本轮授权“开始复测取证”：一次同范围真实 UI 生成，最多三次 completion。
- OpenRouter，`deepseek/deepseek-v4-flash-0731`；温度 `0.2`，最多两个 Agent。
- 沿用零记录合成质检表和原质检分支目标，只授权 `status` 字段的 `update`，最多一行。
- 不执行 Workflow、Evaluation 或写节点，不批准 Proposal，不发布 Xpert，不操作共享栈，不提交或创建 PR。
- 请求 checksum 为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`，与上次一致。
- 这是重新授权的新生成，不是旧不确定请求重放；八份历史账本保持原 hash，旧不确定派发未清除。

源码基线为独立工作树 `modelmirror-meta-planner-controlled-writes-10-closeout` 的
`a7d99925584e818e7f2df84b1646256abfc08ed6` 加现有工作差异。启动前冻结 106 个源码文件。
本次不修改生产代码、Prompt、请求预算、重试策略、300 秒验收截止或旧验收脚本。
仅新增隔离启动/核验脚本和本记录；新账本位于 `.tmp-cw10-transport-retest-20260919/`。

## 真实操作与回执

只重启本任务独立前后端，端口仍为 `15459/16459`。加载标记为
`RETEST_TRANSPORT_SOURCE_LOADED`；新调用账本为零时，核对页面保留的模型、目标与授权，
再真实点击一次“生成候选智能体”。没有通过 API 代替按钮发起生成。

本机时间：2026-09-19 22:15:38 至 22:17:00，UTC-07:00。
Run：`7f8d5db1-e931-42e2-973c-1b40df69ec63`。
Proposal：`proposal_80b1175f8367438eadb0c22fefa7ac1a`，`pending`，revision 1。

| 阶段 | HTTP | 完整返回耗时 | 响应头到达 | Provider 报告 Token |
| --- | --- | --- | --- | --- |
| 任务规划 | 200 | 8.955 秒 | 0.703 秒 | 3,338 |
| 能力编译 | 200 | 61.360 秒 | 0.953 秒 | 16,309 |
| 唯一 Patch 修复 | 200 | 11.486 秒 | 4.016 秒 | 17,805 |

三次均 `finish_reason=stop`；合计报告 37,452 Token，0 次不确定派发。usage 不是独立计费审计。
前端代理耗时 82.749 秒，上游 API 返回 200。API 200 和 Run completed 只代表生成管线返回，
并不代表候选有效。`validation_valid=false`，`candidate_origin=server_synthesized_fallback`。

传输观察器三次均 `status=observed`、单响应、正文完整结束。记录的是原始压缩字节/块的时点，
不能将首字节时点当作首个有效模型 Token，也不保存正文、原始 header 或凭据。

## 失败分层

1. **任务规划通过。** 请求携带的 Schema 与预期一致，Provider、collector 和校验输入的受限结构投影一致。
2. **首次编译候选未满足模型侧 Schema 及后续语义约束。** 存在多余 `values` 输入、未知变量和分支数据不可达，诊断共五项。
3. **Patch 格式与应用通过。** 模型输出 12 个操作：6 个 `disconnect_data`、1 个 `update_node`、5 个 `connect_data`，没有控制边操作。归一化前后均为 12 个，未丢操作。
4. **修复后仍有同一依赖的两项诊断。** `serialize_query.json` 不保证在 `agent_low_score.task` 的所有执行场景存在；`query_json` 的生产者也不属于该 Agent 的控制祖先。
5. **剩余问题不是新增的“缺少模型”。** 后者属于服务端诊断占位图的 Workflow/Publish 校验，不能归因于模型候选未配置模型。

修复前后控制图 checksum 均为
`56ea9c35e2d9e002c2e318d5341a917b6f9548851465c95130f3019281935ee2`。
数据图 checksum 改变，输入契约问题从 1 项降为 0，但原可达性错误 fingerprint 保持一致。
因此本次修复调整了输入/配置，却没有消除原控制路径与数据依赖之间的不一致。

三个阶段的 Provider/collector 内容 checksum 均相同，没有观察到 collector 截断或替换正文。
能力编译阶段的 collector/validator 内容 checksum 不同，受限结构投影相同；代码在
`meta_planner_v2.py` 中先 `GraphIntentV3.model_validate`，再对 `model_dump` 取证。
这里不能宣称校验前后完整 payload 完全相同，且现有安全证据不支持原始 payload 完整重放。

## 下一步边界

下一步应先离线核对修复上下文和路径证明：是否向唯一修复同时提供生产者、消费者、
控制祖先、互斥 outcome 与保证可用变量的完整关系，以及修复要求是否覆盖全部阻塞诊断。
用能复现同一“消费者到达而生产者未到达”问题的确定性夹具证伪，再决定最小变更。
不能靠删除校验、猜测补边、扩大调用次数或继续付费试错达成表面通过。

目前只能确定直接阻塞层与修复未触及的关系；不能断言校验器不存在误判、某条控制边具体放错，
或把深层原因归结为单模型模式上限。本次没有实施后续修复，也没有新增调用授权。

## 完整性和剩余门禁

- `verify.py` 核对 106 个源码 hash、八份历史账本、原 guard/服务器脚本、相同请求和调用上限。
- 只出现一次生成 POST；新增一个不可批准的 pending 诊断 Proposal，未执行审批。
- Workflow/Evaluation Store hash 未变；合成活表仍为 0 条记录、0 次写入。
- `retest-proof.json` 固化安全传输回执；`validation-proof.json` 固化分层诊断，均在忽略目录内。
- 上一批相邻回归 1,242 项通过；全量入口仍为 94 项通过、1 项 worker 断连失败后停止。本次未重跑或声称全量通过。
- 本轮 PR 门禁仍未通过。保留预览器结果供核验，不自动批准、提交、推送或合并。
