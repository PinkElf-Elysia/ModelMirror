# Meta Planner 显式附件视觉契约

状态：Round 6 实施契约；测试和人工验收状态以 [实施记录](./tasks/META_PLANNER_VISION_09.md) 为准。

## 范围

Capability Snapshot V9 仅比 V8 增加 `vision_understanding`，共 19 类。Graph IR 仍为 V3，
Planner 调用仍最多三次。视觉是辅助节点，不能承担计划任务或作为最终输出来源，结果必须
经类型化数据边被下游 Agent 实际消费。公共 App、Structure Evolution、附件遍历和通用
Goal/Handoff 附件委托不在本轮开放。

## 附件与模型

- 用户显式授权视觉节点并单独选择 `vision_model_id`；默认范围不包含视觉。
- 编译器按需增加 `input.selected_file_asset_id` 单附件端口。视觉节点只能直接连接此端口，
  不能从聊天文本、JSON 或前置 Agent 获得资产 ID。
- Planner 只接收槽位契约、模型安全标签与 Managed Binding 摘要，不接收附件内容、
  实际资产 ID、路径或 Base64。
- 服务端固定 Binding 的模型、连接/认证身份、指纹和 checksum；生成、Headless 预览、
  应用、运行预检及实际派发前均重新验证。Binding 不可用或漂移时禁止回退。
- 普通 Workflow 仍读取 `workflow:<workflow_id>`；私有 Xpert 必须启用文件输入且明确
  选择唯一附件。评测保留 `xpert_evaluation` 身份，只读取对应执行项的固定夹具。

## Vision V2

缺少 `contractVersion` 或显式 V1 的旧节点保留原行为，不自动升级。Planner 只编译 V2：

| 参数 | 默认值 | 边界 |
| --- | --- | --- |
| `pdfPageStrategy` | `all` | `all/auto/scanned_only` |
| `maxPages` | 10 | 1–20，按原件总页数拒绝超限，不截取前几页 |
| `maxImageEdge` | 2048 | Planner 范围 512–4096 |
| `failurePolicy` | `continue_on_error` | 或 `strict`；全部失败均进入节点异常策略 |
| 原件大小 | 无 | 最多 10 MiB，PNG/JPEG/WebP/PDF |

每页调用都经过 Managed 执行、统一模型调用预算和并发限制。Managed 返回值必须满足
`ocr_text/visual_summary/tables/charts/language/warnings` 的字段类型与大小要求；只返回
JSON 对象不构成成功。V2 不接受预存 OCR 或缓存分析代替视觉调用，也不自动重试。

## Headless

Adapter 只接受页面策略、页数、边长和页面失败策略。模型、执行版本、输入变量、Binding、
Handle 和 checksum 由服务端推导。编译/反编译和 editor-diff 验证这些受保护字段。
`vision_attachment` 安全预览展示单附件要求、固定模型、Managed 要求与请求数上界。
Apply 重新预检并绑定 Proposal revision、图摘要与候选摘要，只更新 pending Proposal。

## 附件评测

现有 FileAsset API 增加 `purpose=evaluation`，上传范围为 `evaluation:<dataset_id>`，
`input_kind=visual_analysis`。Dataset Case 输入只接受 `attachment={"asset_id":"..."}`。
服务端描述/发布时固定 SHA-256、格式、字节数、页数和作用域；不能由客户端提供这些事实。

可信管理侧通过 `GET /api/files/{asset_id}/download?purpose=evaluation&scope_id=evaluation:<dataset_id>`
下载原件。服务端重新验证用途、数据集作用域、内容 hash 和格式/页数，返回附件响应及
`no-store/nosniff`；不开放其他文件用途或公共 App，API 与报告仍不返回物理路径。

发布版本保留 `evaluation-version:<dataset_id>:<version>` 引用。草稿解除引用不删除
已发布版本原件，普通删除 API 不得解除版本或运行绑定。创建 Run 时固定清单并再校验
内容 hash，每次执行前继续校验；缺失、替换和跨数据集引用均失败关闭。去重后的原件总量
最多 100 MiB、最多 100 条选中用例，不下载导入数据中的 URL 或读取路径。会话导入仍只含文本。

`workflow_vision_match` 消费运行时安全证据：Planner ref、附件 hash、固定模型、页数、
处理状态、块计数和受限内容锚点的布尔结果。报告不保存 OCR、描述或锚点匹配正文；最终
答案继续使用原有指标。没有视觉断言标记证据缺失，跳过视觉但答对不得通过视觉指标，
零选中页不能成为已验证证据。文本模型 override 不修改视觉模型。嵌套 External Xpert
视觉目标因本轮未授权附件传播而被预检阻断。
缺少合法 Planner ref 的手工 V2 节点也在预检阻断，不等待外发后再发现证据无法归属。

## 计量与恢复

已派发页面先持久化执行意图，Managed 回执只保留安全调用状态和 usage。已完成执行项
不重跑；重启时已派发但结果不确定的项失败并标记 `EVALUATION_VISION_DISPATCH_UNCERTAIN`，
不自动重发。取消停止后续派发，已产生的回执继续保留。

视觉 token 只采用 Managed usage；缺失时标记 `vision_token_usage_verified=false`，
不得按 OCR 或提示文本长度估算图片费用。已知 token 与不确定派发分别报告。
实际 usage 在响应后才能获知，预算拒绝会阻止后续派发，但不能撤销已发出的请求费用。
固定附件和 Binding 不保证外部模型完全确定；模拟 Provider 的自动化测试不是线上模型验收。

## 回退

关闭 Planner 视觉 Adapter 与视觉评测入口，保留 V2 运行/读取和 DatasetVersion 附件引用。
不迁移两套附件存储，不删除已有 Proposal、文件或版本，不改变 RAG 视觉处理器主线。
