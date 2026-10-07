# CW10 系统收口后真实复测

日期：2026-09-19。结论：**失败，整轮 PR 门禁未通过**。

## 固定范围

- 用户本次明确要求复测。沿用已批准的零记录合成质检表、相同目标和相同模型，
  一次生成、最多三次 completion，不追加 Evaluation 或业务表执行。
- 工作树：`codex/meta-planner-controlled-writes-10-closeout`，`a7d99925` 加本轮未提交改动。
- 在独立预览器 `15459/16459` 刷新本批源码和前端构建，再通过可见界面点击一次
  “生成候选智能体”；没有直接调用生成 API 代替 UI 操作。
- OpenRouter / `deepseek/deepseek-v4-flash-0731`，温度 0.2，Agent 上限 2。
- 表 `table_7565c77441414604b64c01739977f1d5`，Schema v1；查询授权与写授权分别选择。
  只授权 `update`、`status`、最多 1 行；其余资源、中间件、视觉、insert/delete 关闭。
- 本次冻结请求 checksum 为
  `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`，与前次目标一致。
  守卫未改变；五份历史调用账本保留且 hash 未变。

## 结果

提案：`proposal_7adca8f3dd064129bda65502b2d5e416`，revision 1，`pending`，
`validation.valid=false`，已使用唯一修复机会。

| 阶段 | Provider 回执 | 验证结果 | Provider 报告 Token |
| --- | --- | --- | ---: |
| Task Plan | HTTP 200 / stop | 通过，0 个问题 | 3,358 |
| Graph 编译输入 | HTTP 200 / stop | 拒绝，4 个问题 | 16,305 |
| Graph Patch 修复 | HTTP 200 / stop | Patch Schema 可解析；应用后语义校验拒绝 | 18,859 |

共三次 completion，合计 **38,522 Token**，不确定派发 0；这是 Provider usage，
不是独立账单审计。没有第四次请求或自动重跑。

## 已证实失败链

1. 首轮 `update_status` 使用固定写值与字面值过滤条件，本应只接 `records`，
   却额外接入 `values`。同期还记录了未知变量、不可达输入和控制流诊断。
2. 模型的唯一 Patch 只有两个操作：`disconnect_data` 和 `update_node`。
3. 第一个操作去掉 `values` 后，该节点输入形状一度合法。
4. 第二个操作把过滤条件从 `value_source=literal` 改成 `value_source=input`，
   使必需端口变成 `records + predicate_batch`；但 Patch 没有 `connect_data`，
   最终仍只有 `records`，因此被原子校验拒绝。
5. `failed_phase=patch_apply`，后续重新解析、编译和发布预检均明确为 blocked。
   不能把“问题数从 4 降为 1”解释为其他三个问题已解决。

末次错误：

```text
节点 update_status 的写入输入端口不符：应有 ['predicate_batch', 'records']；
缺少 ['predicate_batch']；重复 []；多余输入位置 []（从 0 计）。
```

三阶段请求均记录 `contract_matches_intended=true`；Provider 到收集器的正文
checksum、收集器到验证器的结构 checksum 一致。现有证据不支持传输或收集器
改写了本次结果。Graph 首次生成的语义错误在 Provider 层即已存在。

权威路由证据正确识别查询输出为 nullable object，`score` 为 required integer。
本次末次失败不是此前的 nullable 路由误判；但由于 Patch 提前阻断，不能宣称
最终控制流、全部类型或实际效果已通过。

## 失败展示与安全边界

- `candidate_origin=server_synthesized_fallback`，
  `graph_ir_status=fallback_unapprovable`。预览器明确提示当前画布为诊断占位图，
  不代表模型成功生成；正向控制流证据不再展示为该候选的成功证据。
- 仅新增一个 pending Proposal；没有批准、发布、运行工作流或执行 Evaluation。
- 指定合成表仍为 0 条记录、0 条写入操作账本。没有写活表、操作共享栈或提交 PR。
- 页面后台尝试刷新视觉模型目录被原有外发守卫拒绝，未派发到 Provider；
  本次三次已授权 completion 均完成，目录警告不能作为生成失败的根因。

## 结论与下一步边界

本次否证了“上一批工程修正已经足以让该固定分支目标通过”的判断。
离线修复及其 1,206 项回归结果仍是有效工程证据，但不是生成鲁棒性证明。
单次结果也不足以证明模型能力上限、单模型架构不可行或需要提前 V4。

下一步建议只针对**配置变更与动态端口、数据边之间的联动契约**做离线复现和
Harness 审查，同时检查唯一修复是否覆盖全部已知独立问题。不能自动猜补记录来源、
放宽端口验证、增加第二次修复或继续消耗额度碰运气。本次复测没有修改生产代码。

最新全量后端仍未完成，Windows Worker 断连阻断保持单列；真实写效果、泛化验收、
帮助中心重放与用户提交批准仍未完成。

## 本地证据

证据位于忽略目录 `.tmp-cw10-system-retest-20260919/`：

- `source-receipt.json`：98 个源码/测试/文档 hash、离线门禁和历史账本绑定。
- `approval.json`：固定请求和空 Evaluation 授权。
- `calls.json`：三次真实派发及安全回执，不保存完整 Prompt/响应正文或密钥。
- `retest-proof.json`：结构诊断、阶段状态、零写入及传输一致性核对。
- `verify.py`：只读复核脚本，不触发模型调用。

```powershell
& C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-system-retest-20260919/verify.py
```

该命令已执行通过。构建产物、运行数据、私有账本与日志均不纳入提交。
