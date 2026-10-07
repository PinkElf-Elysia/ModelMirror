# 输入契约收口后的真实复测

日期：2026-09-19（本地时间）；结论：**本次未完成候选生成，不能判定修复有效，也不满足 PR 门禁。**

## 固定范围

- 工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout`。
- 本地 HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`，加本轮既有未提交修改；未刷新或集成远端。
- 沿用上一轮完全相同的中文质检分支目标，通过独立预览器 `15459` 点击一次“生成候选智能体”。后端为 `16459`，没有操作共享栈。
- OpenRouter / `deepseek/deepseek-v4-flash-0731`；一次生成、最多三次 completion、温度 0.2、Agent 上限 2。
- 只授权零记录合成表 `table_7565c77441414604b64c01739977f1d5` 的查询，以及 `update / status / max_affected_rows=1`。不执行 Workflow 或 Evaluation，不写活表，不批准 Proposal，不发布 Xpert。
- 目标、模型与授权请求 checksum 仍为 `eae0f5aeb6511264f54d13bd3ce96d85ddfddaeb00d7ac0c8483bbce0f5f7fe8`，与前次真实复测一致。生产源码未在本次复测期间修改。

## 实际结果

| 阶段 | 观察结果 | 可证明边界 |
| --- | --- | --- |
| 请求前核对 | 103 个源码/构建/文档文件 hash 固定；历史 7 份调用账本未变；本次账本为空 | 确认加载输入契约收口版本，而非另一个预览进程 |
| 任务规划，第 1 次派发 | HTTP 200，`finish_reason=stop`，约 8.59 秒；Provider 回报 3246 Token | 第一阶段已返回完整响应，流程进入第二阶段 |
| 能力编译，第 2 次派发 | 等待响应正文约 300.003 秒后，被本次验收护栏的 300 秒截止取消 | 未收到可验证的完整编译结果；上游是否完成、是否计费及用量未知 |
| 定向修复 | 没有第三次派发；账本 `halted=true` | 不确定请求没有自动重发，也没有用剩余额度启动新生成 |
| API/UI | 唯一一次生成 POST 返回 HTTP 500；页面提示生成超时、上游完成及计费状态未知 | 不以按钮恢复或旧画布残留作为新候选成功证据 |
| 持久化 | 本次没有新 Proposal；没有进入候选 Schema 校验 | 页面仍显示前次失败占位图，非本次生成结果 |
| 活表核对 | 只读 SQLite 核对仍为 0 条记录、0 条操作账本 | 未发生本次测试表写入 |

已派发 **2 次**，其中完整响应 **1 次**、结果不确定 **1 次**。3246 Token 只是第 1 次已回报用量，不是本次总用量，更不是计费审计结果。没有完整的第二阶段结果，不能比较本批输入诊断是否帮助模型完成修复。

## 已定位与未定位

日志调用链为 `MetaPlannerV2Service.generate -> raw_blueprint = await complete -> collect_chat_completion_text -> httpx response.aread()`，在等待响应正文时触及验收 `guard.py` 的 `asyncio.timeout(300)`。异常是取消后转换出的 `TimeoutError`，不是已返回候选触发的 Schema、端口数量或 Patch 校验错误。

可确认的直接原因是**第二阶段未在固定截止时间内完成响应读取**。现有证据无法区分 OpenRouter 上游排队、模型生成延迟、网络传输停滞或其他上游原因；也不能推断模型能力不足、Graph IR 无法表达该流程或本批修复产生了新的编译错误。第 2 次请求正文为 52881 字节，未保存请求正文或隐藏推理用于报告，大小本身不是延迟根因证据。

启动时另有视觉目录外联被 `ACCEPTANCE_EGRESS_NOT_APPROVED` 拦截的告警；本次没有授权视觉能力。该告警与第二阶段响应读取超时分开记录，不归为编译失败原因。

## 证据与验证

本次证据位于忽略目录 `.tmp-cw10-input-contract-retest-20260919/`：

- `approval.json`、`source-receipt.json`：同范围授权与源码固定记录。
- `calls.json`：实际派发、状态、Provider 回报用量及 checksum；不包含凭据或 Prompt 正文。
- `backend.out.log`、`backend.err.log`：本地 HTTP 结果和超时调用链，不纳入提交。
- `verify.py`、`retest-proof.json`：结果中立的证据核对与安全摘要。

核对命令已执行成功：

```powershell
& 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe' -B '.tmp-cw10-input-contract-retest-20260919/verify.py'
```

核对覆盖 103 个固定文件、7 份历史账本、唯一生成 POST、零新 Proposal、零活表写入和无第三次派发。

- 本次调用账本 SHA-256：`51b5a64adaee069f5a18098f15e7ed5a29c6d109c6ef167e73382de45d1bbe8d`。
- 源码回执 SHA-256：`a3d7c6e44b391eab7bc8bbf56d3d7005db87d8db556f38c45f3b9ea0698a3caa`。

## 门禁与后续边界

- 本次不修改生产逻辑、不延长截止、不放宽校验、不替换模型、不自动再次调用。
- 不确定派发保留锁止，不清空或复用该账本。后续真实复测需新的明确授权和独立账本；不能假定本次未计费。
- 上一批的 1277 项去重相邻回归是离线证据。本次没有重跑全量、构建或隔离三路径效果评测；此前全量入口的 worker 断连失败及后续未运行部分仍未关闭。
- 未证明分支生成稳定性，未完成新候选的三路径效果验证、最新全量与上游集成、最终人工验收。未提交、推送、创建 PR 或进入下一轮。
