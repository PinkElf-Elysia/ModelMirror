# CW10 联合修复后的真实复测

## 结论

用户授权一次复测后，经独立预览器真实点击执行一次生成。结果为 **候选无效，PR 门禁未通过**。
三次 completion 均完整返回；第 2 次用于任务计划修复，第 3 次才进入能力编译。
因此，本次没有执行联合路径 Graph Patch 修复，不能把本次失败说成该修复路径的直接证伪。

运行中曾将第二次返回后的阶段描述为进入定向修复；以最终安全阶段记录为准，更正为上述调用顺序。

## 冻结边界

- 工作树：`C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`。
- HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 保留进入本次工作前已有的 135 项修改/未跟踪路径，本次没有修改生产代码。
- 预览器：`http://127.0.0.1:15459/agents/meta-agent`；仅重启本任务独立后端以加载修复源码。
- Provider：OpenRouter；模型：`deepseek/deepseek-v4-flash-0731`；一次生成，最多三次 completion。
- 合成表：`table_7565c77441414604b64c01739977f1d5`，零记录；仅授权查询及 `update/status/1 行`。
- 目标、授权、八类节点范围和模型不变。请求 checksum：`eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`。
- 本次不执行写节点、不评测、不批准、不发布，不操作共享栈；不在失败后补发调用。

## 三次调用与阻断点

| 顺序 | 阶段 | 结果 | 证据边界 |
| --- | --- | --- | --- |
| 1 | `task_plan` | 任务计划校验失败 | `tasks[0]` 下某字段为 `list_type` 错误；安全证据隐藏了字段名，不猜测具体字段 |
| 2 | `task_plan_v1` | 任务计划修复通过 | 消耗唯一一次修复机会 |
| 3 | `capability_compile` | Intent 解析、授权通过，资源/类型解析失败 | `update_status` 的 `records` 输入被声明为 `any`，不符合目标端口契约；未进入 Native 编译及真实候选发布预检 |

第三次诊断为 `DATA_TYPE_MISMATCH`，位置 `nodes[4].inputs[0]`。
来源身份、端口和变量解析没有报告错误；安全绑定摘要显示来源为真实 Query 的 `result`。
不能把来源身份有效与输入类型有效混为一谈。

报告上的 `missing_workflow_agent_model` 等错误来自服务端诊断占位图，不是模型真实生成图缺失模型的证据。
该占位图不可批准，不计入成功候选。

三次请求均记录 `contract_matches_intended=true`，Provider 到 collector、collector 到 validator 的安全结构投影一致。
这些证据没有显示传输或收集层改写；它们不证明提示词足够有效、模型稳定，或后续尚未执行的校验一定通过。

## 调用与状态证据

- Run：`04d105b6-262e-498d-9083-9b4513c753c4`。
- Proposal：`proposal_586b41c6f6694a009db8a05e83c33baf`，revision 1、pending、validation false。
- 派发 3、完整响应 3、不确定 0、传输失败 0；均为 HTTP 200、`finish_reason=stop`。
- Provider 上报 Token 合计 22,051；未核对实际账单，不将其视为审计费用。
- 核验 113 个冻结源码/测试/文档文件和 10 份历史账本未变化。
- 合成活表复测前后均为 0 条记录、0 条操作。
- Workflow、Evaluation 未执行；没有批准、发布、Commit、Push 或 PR。

安全证据保存在忽略目录 `.tmp-cw10-joint-retest-20260920/`：

| 文件 | SHA-256 |
| --- | --- |
| `retest-proof.json` | `0fdb05abb7df50e114134f33cc14a4444ba689a480a87b42de22d08ed0426330` |
| `calls.json` | `f8e1c9ea46926df87854bfd8a7ea5787de6ee009dd85f8730bcbf2c7fb522400` |
| `source-receipt.json` | `f6d60e50a8303a1c8ace3b4777a78e1a432e6e721638d4bc8e5dce5839cb0744` |

复测证据核验命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B `
  .tmp-cw10-joint-retest-20260920/verify.py 04d105b6-262e-498d-9083-9b4513c753c4
```

## 验证与后续边界

本次重新运行三个联合修复测试模块：23 passed、4 个已有 FastAPI warning。
此前 1157 项集成测试、后端语法及前端构建证据已固定，未在本次重新执行。
此前全量入口仍为 94 passed / 1 failed / 其余未运行，保留独立 `agent_upstream` 断连失败，不能宣称全量通过。

当前可确认的是两个串联阻断：任务计划形状错误提前消耗修复预算，随后图输入类型声明错误阻断解析。
单次复测不足以区分模型能力、上下文分配与契约投影的责任，更不能支持提前切换 V4 多 Agent 的结论。
后续应先用固定安全反例核对这两个阶段的生成契约、示例和诊断，再决定是否需要局部调整。
不放宽 `any` 类型门禁，不增加修复次数，不以再次付费碰运气替代定位；新增修改和真实调用均不由本次复测自动授权。
