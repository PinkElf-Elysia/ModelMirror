# 任务卡：WORKFLOW-VISION-UNDERSTANDING-05

## 1. 单一目标

- 本次要完成：将现有 RAG 视觉处理能力提取为通用多模态服务，并交付可在私有 Workflow、Xpert、Goal 和 Handoff 中执行的 `vision_understanding` 节点。
- 本次明确不做：不创建 RAG Job、Chunk、索引或知识版本；不开放公共 App 文件上传；不强化 Meta Planner。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| 现有 VLM、PDF 渲染和视觉块生成全部耦合在 RAG 处理器 | 已证实事实 | `server/rag/vision_processor.py` |
| Workflow 文件入口当前只允许 `document` 输入 | 已证实事实 | `server/file_assets/registry.py`、`server/file_assets/validation.py` |
| Xpert、Goal 和 Handoff 已在运行元数据中传递显式共享的附件 ID | 已证实事实 | `server/main.py::prepare_published_xpert_run` |

## 3. 影响范围

- 允许修改路径：`server/multimodal/`、`server/rag/`、`server/file_assets/`、`server/xpert_runtime/`、`server/workflow_native/`、`server/xperts/`、`server/main.py`、`server/tests/`、`client/src/components/workflow/`、`client/src/types/`、相关文档与 Harness。
- 禁止修改路径：RAG 持久化协议、知识索引版本、共享 Docker 数据、`.env`、公开 App 上传协议。
- 预计文件数：15-22。
- 影响路由/API：新增 `GET /api/workflow/vision-capabilities`；Workflow 节点契约新增 `vision_understanding`。
- 影响持久化数据：无迁移；Workflow/Xpert 已有 JSON 快照仅新增兼容字段。
- 新增或升级依赖：无，复用 Pillow、pypdfium2、pdfplumber 和 httpx。
- 涉及密钥/网络/文件/子进程/公开访问：VLM 网络调用、受限附件读取；公共 App 明确阻断。

超过 5 个文件时说明无法安全拆分的原因：节点必须同步覆盖后端类型、静态校验、运行器、文件作用域、App 预检、前端注册和测试，否则会出现可拖入但不可执行或可执行但越权读取的半成品。

## 4. 验收标准

### 场景 1

- Given：私有 Workflow 作用域中存在 PNG/JPEG/WebP 或 PDF FileAsset，且选择支持图片输入的模型。
- When：执行 `vision_understanding`。
- Then：输出 JSON-safe 的 OCR、视觉描述、表格/图表块和 warnings，且不创建任何知识版本。

### 失败场景

- Given：附件不在当前作用域、未被 Xpert/Goal/Handoff 显式共享，或模型不支持图像输入。
- When：执行节点。
- Then：安全失败并进入节点既有异常策略，不返回路径、Base64、正文 Prompt 或密钥。

## 5. 实施顺序

1. 模型/契约：通用 Vision 结果、节点字段和类型化输出。
2. 校验/安全：上传类型、作用域、模型能力和 App 阻断。
3. 执行：Classic runner、RunRegistry 安全 checkpoint、RAG 兼容适配。
4. 前端：节点卡片、配置侧栏、附件选择和能力提示。
5. 文档：Harness、Runtime、RAG 与工作流设计。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | `python -m py_compile ...` | 通过 | 已通过 |
| 目标测试 | 新视觉节点、RAG Vision、App 预检测试 | 通过 | `166 passed`；Xpert 修复复核 `57 passed` |
| 回归测试 | `python -m pytest server/tests/ -q` | 通过 | `2804 passed, 29 skipped`；1 个 Coding Project Host 瞬时失败已整文件复跑 `49 passed`；1 个既有 Node 20 直接导入 `.ts` 环境失败 |
| 构建 | `npm.cmd run build` | 通过 | 已通过；附件入口修复后复跑通过，仅既有 chunk size warning |
| 前端回归 | `npm.cmd run test:run -- src/components/workflow/WorkflowRun.file-selector.test.tsx src/components/workflow/WorkflowRun.file-assets.test.ts` | 通过 | `2 passed`、`9 passed`；覆盖节点入口定位运行附件卡片 |
| Docker/人工验收 | 独立预览器 | 用户确认 | 已更新 `127.0.0.1:15283`；节点配置新增附件入口，下拉选项深色样式已验证 |
| 敏感信息扫描 | 扫描 Diff | 无真实密钥、路径或 Base64 数据 | 已通过 |

## 7. 风险与停止条件

- 主要风险：RAG 适配时改变既有缓存或视觉块格式。
- 兼容风险：旧 RAG Pipeline 与旧 Workflow 必须保持行为不变。
- 安全风险：跨作用域附件读取、公共 App 暴露文件能力、日志记录视觉正文。
- 触发停止的条件：必须迁移 RAG 持久化数据、需新增供应商 SDK、或无法在不扩大公开上传面的情况下实现。
- 需要用户确认的问题：无。

## 8. 回退

1. 回滚本轮独立分支提交。
2. 不需要恢复活动知识版本或指针。
3. 不影响既有持久化数据。
4. 回退后运行 RAG Vision、Workflow 和 App 重点测试。

## 9. 完成定义

- [x] 实现只覆盖声明范围。
- [x] 正常与失败路径均有验证。
- [x] 公共接口和数据影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 文档与 Harness 已同步。
- [x] 未知产品信息仍明确标为待确认。
