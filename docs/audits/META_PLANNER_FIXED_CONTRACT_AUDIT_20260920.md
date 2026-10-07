# CW10 任务计划与记录类型固定核对

## 结论

本次为用户授权的固定核对，不是生产修复或真实模型复测。
已定位上一轮任务计划失败的具体字段，并复现一项生成链路处理不一致：

1. `tasks[0].input_contract` 返回了非列表值，违反现有数组契约。它不是 `depends_on` 或 `method_skill_ids` 错误。
2. Query 的权威输出可直接赋给 Update 的 `records`，但模型插入了宽泛 `any` 声明，首次解析因此失败。
3. 同一错误图在现有 Patch 修复路径中，即使模型返回空操作，服务端也会重新推导数据边类型并通过。是否还有修复机会，因而影响同一图的处理结果。

第 3 项是本次新增的工程证据，优先级高于继续叠加提示词规则。
不能据此宣称上一轮真实图整体可通过；原始完整图未保留，离线使用合成结构对照。
也不能据此判定 DeepSeek 或单模型已达能力上限，或提前进入 V4 多 Agent。

## 固定范围与方法

- 工作树：`C:/tmp/modelmirror-meta-planner-controlled-writes-10-closeout`。
- 分支：`codex/meta-planner-controlled-writes-10-closeout`。
- HEAD：`a7d99925584e818e7f2df84b1646256abfc08ed6`。
- 核对真实 Run：`04d105b6-262e-498d-9083-9b4513c753c4`。
- 真实 Proposal：`proposal_586b41c6f6694a009db8a05e83c33baf`。
- 复测证据及调用账本沿用 `.tmp-cw10-joint-retest-20260920/`，不覆盖。
- 113 个已冻结源码、测试和文档 hash 在核对前后均相同；真实调用账本 hash 不变。
- 新增内容仅为忽略目录中的离线核对脚本和本文。没有修改生产代码、已有测试或旧审计报告。
- 脚本拒绝 HTTP、外部 socket 与业务表查询/写入；生成链测试使用 fake completion 和临时 Authoring Store，不执行工作流。

## 事实一：任务计划错误已精确定位

当前契约 `server/meta_agent/schemas.py:140` 为：

```text
input_contract: list[str]
output_contract: str
```

此前安全诊断把字段位置投影为 `tasks[0].<field>`。本次用三个公开列表字段分别构造字符串、null、对象负例，比较既有诊断函数生成的指纹。

| 公开字段 | 与真实错误指纹一致 |
| --- | --- |
| `depends_on` | 否 |
| `input_contract` | 是 |
| `method_skill_ids` | 否 |

唯一匹配指纹：`3d23522c081341e0938acbc4bfa22bdc7166f65ca96cda3422658b37bf29e38e`。
该指纹由位置、标准错误类型及标准错误消息计算，不含原始输入值。
因此可以定位字段，但不能判断真实非列表值究竟是字符串、null 还是对象；本次没有恢复或猜测正文。

首次与任务修复请求的 Schema checksum 均为 `48d5ba074efd9debcf5d1752cfae2817e4271f439caeb28b9c4f3cfece634e2c`，与当前源码生成结果一致。
没有发现 Schema 在传输时漂移。Schema 明确要求数组，但首轮系统说明只说 input contract，未提供任务对象形状示例。
这是可优化的模型提示边界，不是把非法字段自动转换为数组的理由。

## 事实二：记录类型门禁与实际来源

生成 Schema checksum `99b5b6826b10240daa4087eef90686713573831dadc9532598cceb6c2dd352f9` 与真实第三次请求一致。
`generation_contract.py:131` 的模型声明约束和最终类型检查都拒绝裸 `any`。

合成图保持节点、资源、控制边、变量来源与配置不变，仅改变 `records.value_schema`：

| Query 模式 | records 声明 | 模型 Schema | 最终编译 |
| --- | --- | --- | --- |
| first | any | 拒绝 | 拒绝 |
| first | object + nullable | 接受 | 接受 |
| first | object，未声明 nullable | 接受 | 拒绝，真实来源允许 null |
| first | array<object> 或 null | 接受 | 拒绝，与真实来源形状不符 |
| list | array<object> | 接受 | 接受 |
| list | object | 接受 | 拒绝，与真实来源形状不符 |
| list | any 或 array<any> | 拒绝 | 拒绝 |

JSON Schema 是局部声明边界；是否接受真实上游类型仍由解析器判断。不能把局部 Schema 接受直接当作可执行。
对 `first + any` 反例，诊断同时证明 `source_assignable=true`：源与目标本身兼容，阻断来自中间声明，而非真实 Query 缺失可信记录身份。

## 事实三：首次编译与 Patch 处理不一致

调用点证据：

- `graph_ir_v3.py:1209`：从 Adapter 和资源快照确定输出类型。
- `graph_ir_v3.py:1325`：继续用模型声明校验数据边；没有对该输入同步采用权威派生类型。
- `meta_planner_v2.py:973`：已有 `_normalize_adapter_outputs_for_repair` 同时重建输出及匹配的数据边类型。
- `meta_planner_v2.py:3633`：该函数只在 Patch 应用后调用，随后重新编译。

生成服务整链对照如下，所有调用均为 fake completion：

| 首次任务计划 | 图的 records 声明 | 唯一修复用于 | 调用数 | 结果 |
| --- | --- | --- | --- | --- |
| input_contract 错误 | any | 任务计划 | 3 | 图解析失败 |
| input_contract 错误 | 正确 | 任务计划 | 3 | pending 有效候选 |
| 正确 | 正确 | 无 | 2 | pending 有效候选 |
| 正确 | any | Graph Patch，返回零操作 | 3 | 服务端重新推导类型后得到 pending 有效候选 |

这证明修复预算没有超发，但确定性类型处理被绑定到了修复阶段。
第一行复现真实失败的阶段顺序；最后一行证明通过并不一定代表模型修复了错误。

五个负对照在现有归一化后仍失败：未知来源、未知端口、变量冒用、缺少控制先后、未授权字段。
这些测试只覆盖上述边界，不能据此把现有归一化函数整体提前：它会重写所有 Adapter 输出及对应输入，仍需限制权威派生范围，保护显式类型冲突和其他节点行为。

## 提示词与 Harness 核对

已证实：

- 首次请求的 Schema 已包含正确约束；模型没有遵守，不是系统完全未告知。
- Query 的短端口描述保留通用 `type=any + any_of`；具体 `first/list` 形状在自然语言规则中说明。
- 表节点输入端口展示被整体移除，以避免把动态谓词族当作具体端口；因此固定 `records` 的类型信息也不在短端口展示中，只保留必填和来源规则。
- 最小图示例只展示 Agent，不展示 Query → records 的绑定形状。
- 同 scope 合成 Prompt 中 `required_schema` 为 19,425 字节，`graph_intent_contract` 为 14,571 字节，整个用户 Prompt 为 41,340 字节；这些不是上一轮完整 Prompt 的逐字重放。
- 真实第三次调用上报 13,152 输入 Token、2,447 输出 Token，正常 stop；没有输出截断证据。
- 当前 legacy 入口 `server/main.py:9420` 请求 `json_object`，不是 Provider 端严格 Schema 解码。`required_schema` 作为提示内容发送，随后本地验证。不能把 Schema 一致性误称为 Provider 强制执行。

合理推断：短规则、通用 union 与较深 Schema 的分散表达增加模型检索和复述类型的负担。上下文负担值得收敛，但本次没有注意力测量或不同模型的受控比较，不宣称它已被证明是全部失败根因。

## 最小修复建议，尚未实施

1. **统一权威类型处理。** 首次生成与 Patch 共用同一受信任资源类型解析语义。仅在授权、固定 Schema、来源 ref/port/variable 均验证后派生直接资源数据边类型；继续验证真实源到目标的兼容性、可信记录来源、控制可达性及写授权。不能简单删除 `any` 门禁或让任意声明覆盖真实类型，也不能把现有修复函数无条件前移。
2. **精简并对齐模型输入。** 从同一契约投影 `first=nullable object`、`list=array<object>`、Insert=object 的短形状说明；明确通用 union 不是裸 any。任务计划给出 `input_contract` 数组与 `output_contract` 字符串的极简字段片段，不堆新一轮长篇规则，不增加调用。
3. **以交叉路径反例验收。** 保留此次四种预算/阶段组合及九种类型对照；同一语义图不能因修复机会在哪一阶段耗尽而改变权威类型处理。显式不兼容类型、伪造来源、越权字段与不可达数据继续拒绝。完成离线收口后再单独授权真实复测。

不修改 Runtime、写入协议、三次调用上限或 22 类能力范围；不通过切模型、多 Agent、增加重试或放宽类型验证掩盖当前证据。

## 验证结果与保留边界

- 固定核对首次 15 passed；补充整链空 Patch 与五组负例后，同一脚本重跑 **21 passed**、4 个已有 FastAPI warning，16.39 秒。两次有重叠，不相加。
- 既有记录 Schema 投影、生成契约、输入契约对齐及任务图契约交叉检查：**111 passed**、4 个已有 warning，98.18 秒。
- 通过表示预期拒绝、合法对照和阶段差异均被复现，不表示真实模型生成稳定。
- 本次未运行全量后端、前端构建或真实预览器调用。之前的全量入口仍为 94 passed / 1 failed / 其余未运行，不覆盖或升级为全绿。
- 没有新 Provider 调用、凭据读取、业务表读写、服务重启、共享栈操作、审批、发布、Commit、Push 或 PR。

命令：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-cw10-closeout/run_offline_v2.py `
  .tmp-cw10-fixed-check-20260920/test_fixed_check.py `
  --junitxml=.tmp-cw10-fixed-check-20260920/fixed-check-v2.xml

& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -B .tmp-cw10-closeout/run_offline_v2.py `
  server/tests/test_meta_planner_record_schema_projection.py `
  server/tests/test_meta_planner_generation_contract.py `
  server/tests/test_meta_planner_input_contract_alignment.py `
  server/tests/test_meta_planner_task_graph_contract.py `
  --junitxml=.tmp-cw10-fixed-check-20260920/contract-regression.xml
```

忽略目录证据：`findings.json` SHA-256 为 `37abd3e88f92509e43b9a44a2f62ecaf91a44373af7f733cf509c3f63cfa8ca9`；脚本 SHA-256 为 `9be1f541a76dafb8d309cb4dedfaa36bff92983e598c121a35f8d687cee58742`。
生产文件无改动，不需要运行回退；如不保留本次审计，只撤销本文和本次忽略目录产物，不覆盖旧证据或脏分支。
