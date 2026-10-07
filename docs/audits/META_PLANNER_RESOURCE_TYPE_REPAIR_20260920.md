# CW10 权威资源类型与短契约修复

## 范围

本次响应“开始修复”，依据 [固定核对](./META_PLANNER_FIXED_CONTRACT_AUDIT_20260920.md)。
只执行离线最小修复与回归，不追加真实模型调用，不更换模型或提前进入多 Agent。

- 工作树：`C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`。
- HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 开工时 137 项修改/未跟踪路径，暂存区为空；保留已有工作，不重置主工作区。
- 修复前快照与 hash：忽略目录 `.tmp-cw10-resource-types-20260920/`。
- 上轮真实调用账本及固定核对证据保持原样。本次合成对照不是完整原始响应的逐字重放。

## 修复决策

| 证实问题 | 处理 | 保留门禁 |
| --- | --- | --- |
| 首次解析拒绝宽泛资源输入声明，空 Patch 却覆盖声明后接受 | 在 Graph IR 中统一：真实来源必须符合声明约束，真实类型必须符合目标端口；通过后数据边采用真实类型 | 不信任客户端类型、身份、资源版本或 Handle；非资源连线维持原契约 |
| 空 Patch 也覆盖显式冲突 | 停止资源输入的自动覆盖；错误 `first -> 非 nullable object` 等在两条路径均拒绝 | 来源、端口、变量、授权、控制可达性、同表收据和影响上限不变 |
| 派生 V3 输入后兼容元数据仍残留 any | V3 与兼容端口元数据使用同一已验证输入 | 不修改节点执行、变量或调度；规范化 checksum 与编译往返须一致 |
| 无快照的 Query 输出通用 union，Insert 误返回 matched/affected 形状 | Adapter 在配置已知时确定 first/list/Insert 的基本形状，字段仍由授权快照解析 | 无快照不等于资源可用或已授权，仍须完整资源门禁 |
| 任务输入数组与输出字符串缺少短说明 | 从现有任务 Schema 投影两个极简字段片段；三种图生成阶段共用 Adapter 形状 | 不自动转换非法 task 字段，不增加修复或调用次数 |

模型生成 Schema 仍引导符合目标端口的具体记录形状，没有改成任意 `any` 放行。公开 IR
允许的宽泛声明只是对来源的描述上界；仅已验证的直接资源来源可以提供真实细化类型。
`array<any>` 等较宽描述也必须由真实的 `array<object>` 来源证明，不能用于伪造记录身份。
来自纯节点、用户文本或 Agent 的 `any` 不获得这项资源权威。

此次没有把旧归一化函数整体前移。纯节点既有修复行为保留；本次只收口资源连线，不能把
测试结论外推为所有节点类型处理都已重构。运行期写入、NodeContract、22 类能力、3 次
completion 上限、SSE、审批和实际事务语义均未变。

## 红测与独立批次

1. 资源阶段对照红测：16 failed / 13 passed。其中一项负例直接调用底层编译辅助函数，
   未经过服务的授权预检；将该测试改为覆盖真实调用顺序，没有扩大底层函数权限。
2. 初次修复回归：108 passed / 3 failed。两项精确定位到 `plannerInputs[0].value_type`
   残留；另一项旧正例依赖空 Patch 擦除资源类型冲突。同步兼容元数据，并将正例改为
   显式重连两条资源边；保留空 Patch 拒绝测试，不降低最终断言。
3. 重跑相同资源/纯节点/Headless 回归：111 passed，4 个已有 FastAPI warning，35.76 秒。
4. 提示与形状红测：8 failed，包含 Query 两种模式和 Insert 错误返回形状的独立断言。
5. 同源提示、任务契约、资源类型、生成 Schema 与旧记录投影：144 passed，4 个已有
   warning，73.34 秒。
6. 扩大回归两次均为 1193 passed / 1 failed：第一次发现端口名称不应删除，恢复名称后
   第二次指出类型也属于既有投影协议。撤回删除端口信息的方式，改为保留全部端口属性、
   只省略 Schema 的可还原默认值。两个既有断言均保留，失败证据不覆盖。

新增 `test_meta_planner_resource_type_resolution.py` 和 `test_meta_planner_prompt_shapes.py`，
共 37 项。HTTP、外部 socket 及业务记录访问由离线守卫拒绝；生成服务使用 fake completion。
四组“任务修复是否已用尽 x 记录声明是否宽泛”均应生成 pending 有效合成候选，分别只用
3/2 次 fake completion。合法宽泛描述不再消耗模型修复；显式冲突不会因空 Patch 消失。

## 集成验证

最终代码上的验证结果：

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| 所有 `test_meta_planner*.py` 加 11 个相关模块 | 1194 passed，4 个既有 warning，295.11 秒 | 覆盖 Registry、Authoring、Publish、Evaluator、Evolution、App、typed Workflow 和 Agent Table |
| 8 个受控写入 Runtime/Store/Evaluator 模块 | 169 passed，4 个既有 warning，37.82 秒 | 合成临时 Backend；不能替代真实业务写入验收 |
| 全量入口 `server/tests/ --maxfail=1` | 94 passed / 1 failed，53.58 秒；其余未运行 | 不宣称全量通过 |
| Python 语法 | 通过 | 3 个生产文件、3 个测试文件与 `server/main.py` |
| `client` 的 `npm.cmd run build` | 通过 | 保留大体积 bundle warning；未重启或操作预览器 |
| Diff、UTF-8 与敏感模式扫描 | 通过，8 个本次范围文件无匹配 | 模式扫描不等同完整安全审计；暂存区为空 |

全量入口失败仍为
`test_agent_upstream_port.py::test_started_worker_crash_after_model_request_is_never_restarted`：
`server/agent_upstream/port.py:541` 的 `stdin.drain()` 在 worker 断连处理时抛出
`ConnectionResetError`。与修复前相同，相关两个文件相对 HEAD 无 Diff。本次没有修改该
模块，也没有重新证明它在干净 `origin/main` 上的表现，因此只标为本次修复前已有阻塞。

上述测试通过现有隔离运行器执行，禁止外部网络和 dotenv 加载，所有 Store 指向新建
临时目录；新测试额外拒绝业务记录访问。代表性命令：

```powershell
$tests = @(Get-ChildItem server/tests -Filter test_meta_planner*.py |
    Sort-Object Name | ForEach-Object { 'server/tests/' + $_.Name })
$extra = @('test_meta_agent', 'test_workflow_node_contracts', 'test_xpert_runtime_authoring',
    'test_xpert_publish', 'test_xpert_evaluations', 'test_xpert_structure_evolutions',
    'test_xpert_app_api', 'test_workflow_typed_values', 'test_workflow_typed_ai',
    'test_agent_tables', 'test_workflow_data_table_nodes') |
    ForEach-Object { 'server/tests/' + $_ + '.py' }
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B `
    .tmp-cw10-closeout/run_offline_v2.py @tests @extra
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B `
    .tmp-cw10-closeout/run_offline_v2.py server/tests/ --maxfail=1
```

测试分组存在重叠，不累加成独立样本数。最终 140 项修改/未跟踪路径，新增的三项为本文
及两个测试模块；没有新增待提交的数据库、日志、Runtime Store、上传文件或构建产物。

## 提示体积假设的证伪

固定合成范围的图生成提示由 41,340 增至 41,401 UTF-8 字节，增加 61 字节，约 0.15%。
其中 `graph_intent_contract` 由 14,571 增至 14,632 字节，`required_schema` 保持 19,425 字节。
“此次提示总量会缩短”的测量断言失败，原断言和失败 XML 保留；没有删减契约来追求绿测。
42 项兼容检查在同次测试中通过，唯一失败就是这项体积假设。

实际改进是同源的短字段说明及模式形状明确化，并非已证明的 token 节省、注意力提升或
成功率提高。该测量不包含真实模型响应，不是原始提示的逐字重放，也未测量 token。

安全证据保存在忽略目录 `.tmp-cw10-resource-types-20260920/`：

| 文件 | SHA-256 |
| --- | --- |
| `integration-regression-v3.xml` | `0ee00a64b654bf5025390d1713b67bcf70a074c70949db756ec16f43efb3fb95` |
| `full-backend.xml` | `e45b57df8bbcef8f130da46bb5653021ed232cf18cbb04bd69cce1af946a97f7` |
| `prompt-measurement.json` | `0f0968493e19cfb01497fd8d3a240b457ae46598aec71a2edb26a0dfd457ced7` |
| `prompt-measurement.xml` | `70a28845a78533ae88fb7e00f66838e497f6202e4aa017ab86e373e99eee4109` |

上轮真实复测的三个证据文件及固定核对的两个证据文件 SHA-256 均保持不变。

## 兼容、回退与剩余门禁

生产改动限于 `graph_ir_v3.py`、`meta_planner_v2.py`、`node_adapters.py`，另更新一个已有
测试并新增两个测试模块。模块说明与本文随代码同步，无数据迁移或生产依赖。

回退只反向撤销本次快照对应的局部 Diff，不回滚整条脏分支，不覆盖历史账本。旧
Workflow、Proposal 和已发布版本不自动重写；新候选保存权威输入类型。

本次不读取凭据，不读写活表，不重启预览器或共享栈，不审批、不发布、不 Commit/Push/PR。
真实 Provider 复测、泛化稳定性及人工验收仍未覆盖，须另行授权与取证；不能因此宣布
CW10 已达到提交门禁。
