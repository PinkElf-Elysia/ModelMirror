# CW10 传输阶段证据最小收口

## 范围与不变量

本批仅补被动传输证据与离线证伪，不将观测改进描述为模型超时已修复。
最新冻结实测的第二次 completion 在响应正文读取阶段被本地 300 秒验收截止中断；
响应头已经到达，但旧回执只有完整正文读完后才写入状态，无法判断首字节与中途停顿。
上游生成、排队、网络及实际计费原因仍未证实。

- 不改变 Prompt、模型、请求正文、Token 上限、三次调用预算、300 秒验收截止或重试策略。
- 不改编译器、授权、校验器、Managed 调用链、Runner、SSE 或审批语义。
- 不改历史验收脚本与冻结账本，不给历史请求补造回执。
- 不调用真实 Provider，不重启预览器，不操作共享栈或业务表，不提交或创建 PR。
- 只修改 `server/main.py`、`server/meta_agent/generation_evidence.py`，新增独立观察器、测试和本记录，共五个文件。

## 验证与回退

验收必须区分响应头之前超时、响应头之后无正文、部分正文后停顿；同时验证取消、
完整成功、原请求/usage 不变、异常不被替换、观察器故障降级和敏感内容不落证据。
测试仅使用 MockTransport 或本机隔离传输，不作为真实 Provider 成功证据。

回退仅移除 collector 的传输观察接入；已有请求/响应/校验证据及业务行为保持原样。
本批不重新打开已消耗的实测授权。

## 证据语义

- 仅在现有 `GenerationEvidence.capture` 上下文内、使用 legacy collector 原生 HTTPX 发送时启用；普通聊天和自定义 sender 不安装观察 hook。
- 复用 `generation_evidence.calls[].transport` 进入现有失败 Run 元数据；没有新增 Store、后台任务或外发请求。
- 记录连接/TLS、请求发送、响应头与正文阶段的受限 trace 时点；只接收固定事件名，忽略 trace 的 `info` 内容。
- 响应 hook 到达时记录 HTTP 状态和相对时间；正文流只统计原始压缩字节数、块数、首/末字节时间，不缓存、解码或改写正文。
- `body.complete` 仅表示底层流正常结束，`outcome=completed` 仅表示 HTTPX 调用返回；不代表 JSON 合法、Planner 校验通过、Provider 计费已确认或候选生成成功。
- `idle_ms_at_end` 只在已观察到正文、但正文未完成时给出。HTTPX 已预加载的响应标记 `preloaded`，不伪造首字节时间或网络字节数；不能把这种情况下的计数 0 当成真实零字节。
- 单次调用出现重定向或认证多响应时标记 `multiple_responses`，状态与正文统计明确只针对第一份响应，不能按单响应证据判定最终上游状态。既有行为不被阻断或改写。
- 只保存 `x-request-id / x-openrouter-request-id / cf-ray` 的 SHA-256，单值超过 512 字符则省略；不保存原始 header、原始关联 ID、Cookie、凭据、URL、异常消息或正文。哈希用于本地配对，不能直接当作上游工单查询 ID。
- 观察器故障标记 `partial`，不替换业务返回或异常；`partial` 不能用作完整阶段证据。取消原样传播，无自动重试。

HTTPX 0.28.1 的本地执行顺序与[官方事件 hook 文档](https://github.com/encode/httpx/blob/master/docs/advanced/event-hooks.md)均确认响应 hook 先于自动正文读取；正文包装继续委托原流的迭代与关闭。离线回环测试使用真实 HTTPX/httpcore 和 gzip 响应验证此边界，不是外部模型验收。

## 结果

本批观测缺口已完成局部修复；**没有证明上游超时消失，也没有达到 CW10 PR 提交门禁**。

| 检查 | 实际结果 |
| --- | --- |
| 新增传输证伪 | 17 项通过，已包含在下方相邻回归中 |
| 传输、生成证据与边界定向组 | 66 项通过；与相邻回归重叠，不累加 |
| Meta Planner 与相邻模块回归 | 1242 项通过，339.38 秒；`adjacent.xml` |
| 全量 `server/tests/` 入口 | 94 项通过、1 项失败，首败停止；`full.xml` |
| 四个本批 Python 文件语法 | 通过；pycache 仅写入本批临时目录 |
| 前端生产构建 | 通过；输出到本批临时目录，未覆盖预览器 `client/dist` |
| 差异与敏感扫描 | `git diff --check` 通过；五个文件的常见密钥模式零命中；证伪测试同时确认正文、异常消息、header 秘密不进入证据 |

全量失败仍是 `test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`：
`server/agent_upstream/port.py:541` 的 `stdin.drain()` 在 worker 断连后抛出 `ConnectionResetError`，
覆盖原 `EngineUnavailableError`。与上一批失败一致，相关实现和测试对本地 HEAD 无差异；
本批没有修改或绕过它。后续全量测试未执行，不能称为全量通过，也未刷新或验证远端基线。

首次沙箱内测试进程未返回测试结果，已停止；之后以同一隔离脚本在获准环境运行完成。
首次尝试不计作代码失败或修复前红测。构建保留既有大分块告警及临时 outDir 提示，
后端保留既有 FastAPI 生命周期与 PyPDF2 弃用告警。

### 验证命令

工作目录为 `C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout`。
脚本拒绝外部网络、清除凭据环境并将所有 Runtime Store 重定向至本批独立临时空间。

```powershell
$python = 'C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe'
$runner = '.tmp-cw10-closeout/run_offline_v2.py'
& $python -u $runner server/tests/test_meta_planner_transport_evidence.py server/tests/test_meta_planner_generation_evidence_integration.py server/tests/test_meta_planner_generation_boundary_audit.py
$tests = @(Get-ChildItem -LiteralPath server/tests -Filter 'test_meta_planner*.py' | Sort-Object Name | ForEach-Object { 'server/tests/' + $_.Name }) + @('server/tests/test_meta_agent.py', 'server/tests/test_workflow_node_contracts.py', 'server/tests/test_xpert_runtime_authoring.py', 'server/tests/test_xpert_publish.py', 'server/tests/test_xpert_evaluations.py', 'server/tests/test_xpert_structure_evolutions.py', 'server/tests/test_xpert_app_api.py', 'server/tests/test_workflow_typed_values.py', 'server/tests/test_workflow_typed_ai.py', 'server/tests/test_meta_agent_managed_endpoints.py', 'server/tests/test_meta_agent_managed_gateway.py', 'server/tests/test_xpert_runtime_chat.py', 'server/tests/test_workflow_managed_runtime.py', 'server/tests/test_workflow_agent_task_node.py', 'server/tests/test_workflow_resource_nodes.py', 'server/tests/test_workflow_content_policy_runtime.py')
& $python -u $runner @tests --junitxml=.tmp-cw10-transport-evidence-20260919/adjacent.xml
& $python -u $runner server/tests/ --maxfail=1 --junitxml=.tmp-cw10-transport-evidence-20260919/full.xml
& $python -X pycache_prefix=.tmp-cw10-transport-evidence-20260919/pycache -m py_compile server/main.py server/meta_agent/generation_evidence.py server/meta_agent/transport_evidence.py server/tests/test_meta_planner_transport_evidence.py
# 在 client 目录运行：
npm.cmd run build -- --outDir ../.tmp-cw10-transport-evidence-20260919/client-build
```

### 冻结与权限核对

- 旧源码回执中的 103 个文件，仅 `server/main.py` 与 `server/meta_agent/generation_evidence.py` 两个允许文件改变，其余 101 个 hash 一致；本批另外新增观察器、测试及本文档。
- 七份历史调用账本与本次冻结调用账本均未变化；冻结 `calls.json` SHA-256 仍为 `51b5a64adaee069f5a18098f15e7ed5a29c6d109c6ef167e73382de45d1bbe8d`。
- 原 300 秒验收 guard hash 仍为 `f7cad81a3684ad25fe4266e5382a8c8f54be3a542979190de2cc1e381056cba6`，未清空不确定派发锁止。
- 前后端预览进程仍为原 PID 35148 / 52136，未重启，**尚未加载本批新观察器**。
- 本批没有真实模型派发、共享栈操作、业务表写入、Proposal 批准、提交、推送或 PR。JUnit、pycache 与构建目录均被 Git 忽略。
- 后续真实复测仍需新授权、新独立账本和明确加载本批代码；不能复用旧请求或假定此前超时未计费。
