# CW10 分支复测失败的离线复现

日期：2026-09-19。范围：定位与复现，不修改生产代码，不追加模型调用。

## 结论

**已复现配置与数据边不同步的失败，尚未修复真实生成能力，PR 门禁仍未通过。**

当前证据将问题收敛到模型生成/一次性修复的完整性：首次候选有多个独立错误，
修复只处理了部分问题，又改变了动态端口需求。当前 Adapter 和原子 Patch 并非
无法表达正确结果：同样的分支、资源和授权约束下，完整的显式修改可以通过。

不能据此断言 DeepSeek 的能力上限，也不能证明提前多 Agent 或更换模型即可解决。
下一步应先收敛生成/修复 Harness，而不是放宽执行校验或自动猜补数据边。

## 基线与证据边界

- 工作树：`C:\tmp\modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`。
- HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`，包含本轮既有未提交改动。
- 真实失败 Proposal：`proposal_7adca8f3dd064129bda65502b2d5e416`。
- 与真实复测绑定的 98 个源码/测试/文档 hash 未变；调用账本仍是原来三次，未追加。
- 真实复测的完整模型正文没有持久化，本次不能宣称逐字重放原始 GraphIntent。
  使用保留的端口、配置模式、两次操作和诊断证据构造同构局部反例，并另建完整分支对照。
- 原始真实证据见 `META_PLANNER_CONTROLLED_WRITES_SYSTEM_RETEST_20260919.md`。

## 对照结果

| 对照 | 实际结果 | 可以证明的事实 |
| --- | --- | --- |
| 固定写值，额外接入 `values` | 拒绝 | literal 写值不应同时绑定 values |
| 先删除 values，再把 filter 改成 input，但不连 predicate_batch | Patch 整批校验拒绝 | 与真实失败的操作类型、输入数量及合法/非法变化一致；缺少 predicate_batch |
| 仅删除 values，保留独立的控制先后缺陷 | Patch 形状通过，整图授权/控制检查拒绝 | 修复一个端口问题不等于修复全部已知错误 |
| 更新 filter、显式连接 input.user_input 到 predicate_batch，同时修复对照图中的控制顺序 | 校验、编译、反编译再编译通过 | 当前契约可以表达同一业务约束的合法分支图 |
| 将连接端口与修改配置两个操作互换顺序 | 两种顺序均通过 | 已按整批末态校验，不是提前检查中间状态导致误拒绝 |
| 使用整条 Query 记录作为 batch 标量谓词 | 拒绝 | 不能靠补任意一条边绕过字段类型契约 |
| 两操作失败 Patch 的 JSON Schema 检查 | Schema 通过，语义应用拒绝 | 格式合法不等于图编辑结果合法；这不是应放宽的误报 |

所有失败对照均验证原 GraphIntent 不被原地修改。

另外通过脚本化 completion 驱动真实 `MetaPlannerV2Service.generate` 三阶段：

- 两操作失败修复：停在 `patch_apply`，后续 resolve/compile/publish_preflight 为 blocked。
- 只删除多余输入：进入后续检查后仍存在 `DATA_UNREACHABLE`，候选无效。
- 完整对照修改：候选静态验证通过；只创建临时目录中的 pending Proposal，不创建 Xpert 草稿。

这三条链路均恰好调用三次**本地脚本 completion**，不是 Provider 调用，不作为真实生成成功率证据。
没有执行 Workflow、Evaluator、写节点或普通业务表 CRUD。

## Harness 核对

已证实，当前修复 Prompt 并非没有相关规则：

1. `repair_contract.config_edit_sequence` 已要求先形成完整最终 config，再显式删除/补齐数据边。
2. `input_binding_contract` 已说明 literal/input、records/values、`predicate_{ref}` 的区别。
3. 复现中四项独立诊断全部进入 `validation_issues` 和 `validation_frontier`，没有只发送第一条错误。
4. Patch 原子应用在最后统一验证节点形状；完整配置与连线修改可交换顺序。

本次离线构造的修复 Prompt 为 65,024 UTF-8 字节，其中：

| 内容 | 字节 |
| --- | ---: |
| required_schema | 20,386 |
| graph_intent_contract | 13,626 |
| base_graph_intent | 10,805 |
| validation_frontier | 7,462 |
| repair_contract | 4,991 |
| validation_issues | 361 |

还投影了 19 类 Patch 操作，包括本例未授权资源对应的绑定操作语法；授权门禁仍然有效，
这不是越权漏洞。它说明唯一修复仍需在较大的通用上下文中自行整合多个相关约束。

上述字节数是离线重建数据，不能冒充真实调用的完整 Prompt 分解。
真实账本只证明最后一次请求为 76,478 字节、Provider 报告 prompt_tokens=18,737、
completion_tokens=122、finish_reason=stop，没有截断回执。

**合理推断而非已证实因果**：注意力分配和任务负担可能影响一次修复的完整性。
不能以这些长度数字直接认定上下文溢出或模型能力不足。继续增加同义规则缺乏依据。

## 后续最小范围建议

尚未实施，需在修复任务中单独确认：

1. 以同一 Adapter/校验器派生“配置、必需端口、具体来源、控制先后”的关联修改要求，
   让修复输入明确区分当前状态、最终必须满足的条件、独立错误和不可推断的事实。
2. 收敛一次修复的上下文，优先提供受影响节点、关联边与逐项诊断；保留授权和全图安全门禁，
   不再重复扩写同义提示，不把多份相近契约交给模型自行合并。
3. 先对 literal/input 切换、嵌套谓词、多输入、无效来源及独立控制缺陷建立离线反例矩阵。
   以完整修复与不完整修复对照证明契约，不以测试数量替代真实泛化验收。

禁止自动选择 records 来源、自动连 predicate 边、静默改写业务 filter、增加第二次修复、
弱化原子校验或扩大表授权。现有安全边界与三次 completion 上限不变。

## 执行记录

隔离复现文件：`.tmp-cw10-reproduction-20260919/test_repro.py`，未纳入生产测试或提交范围。

```powershell
& C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B -m pytest `
  .tmp-cw10-reproduction-20260919/test_repro.py -q -s -p no:cacheprovider `
  --tb=short --basetemp=.tmp-cw10-reproduction-20260919/pytest-run-02 `
  --junitxml=.tmp-cw10-reproduction-20260919/reproduction-02.xml
```

结果：**11 passed in 5.91s**。重新执行时必须选用新的、已核验不存在的 basetemp，
不要让 pytest 清理既有证据目录。

前两次尝试：8 passed / 3 errors。第一次为默认 Windows pytest 临时目录权限错误；
第二次为测试自带网络守卫拦截了 asyncio 本地 self-pipe。改用独立临时目录，
仅允许标准库 `_fallback_socketpair` 连接它刚创建的本地监听端点后，同一 11 项全部通过。
外部网络、HTTP 派发和业务记录读写仍由测试守卫拒绝。以上是测试环境问题，未作为生产修复。

附带发现：旧 `verify.py` 再运行时将 `re.findall` 返回的 tuple 与 JSON 读回的 list
直接比较，导致断言失败；唯一差异是 `post_requests` 的容器类型，JSON 规范化后全结果相等。
原始证据与账本没有变化。该证据脚本问题与 Planner 失败无关，本次未修改原脚本。

本次不运行全量回归或前端构建，不刷新预览器、不接触共享栈，不提交、推送或创建 PR。
真实泛化生成、实际写入效果、最新全量和人工门禁仍不能由这些离线检查替代。
