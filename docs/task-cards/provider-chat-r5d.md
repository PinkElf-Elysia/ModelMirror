# 任务卡：Provider Chat R5D

## 1. 单一目标

- 将 `gateway=default` 的 MCP 工具模式与受控文件输出分别接入
  `chat_tools`、`chat_file_output` 的 Managed Provider 路由和 v15 Receipt。
- 保留既有 Runtime 工具执行、权限、文件渲染、SSE 与前端交互契约。

## 2. 已证实基线

| 结论 | 证据 |
| --- | --- |
| 当前基线 | `origin/main@20dd124e364701ee6ac569cc8de15d102eb1a07b` |
| R5A 已保存逐能力认证、路由和资格 | `server/model_router/chat_control.py`、`chat_certification.py` |
| R5B 已接管稳定白名单普通文本 | `server/model_router/chat_stable.py`、`server/main.py` |
| 工具模式仍读取旧静态网关 | `server/main.py::stream_chat_toolset_text` |
| 文件输出仍只认可旧固定 OpenRouter 目标 | `server/main.py::validate_chat_output_request` |

## 3. 范围与风险

- 允许修改：Provider Chat 稳定路由、`/api/chat` 工具/文件分支、文件输出发送注入、
  对应测试、设置说明和控制面文档。
- 禁止修改：公开 Chat 请求与 SSE 成功契约、工具执行权限、文件 renderer、多模态、
  Canary、Auto 选路、Agent、Workflow、RAG、Coding、模型目录口径与计费。
- 数据影响：不迁移 Schema；继续写入现有租户隔离的 v15 run/attempt Receipt。
- 依赖影响：不新增或升级生产依赖。
- 主要风险：多步模型调用跨 Provider 重放、策略漂移后继续派发、能力认证互借、
  Receipt 或日志保存用户/工具/文件正文。

## 4. 实现约束

1. 只有精确模型、当前连接指纹和对应能力认证均有效时才选择目标。
2. `chat_text` 认证不能替代 `chat_tools` 或 `chat_file_output`。
3. 第一次 Provider 派发前允许选择显式备用；派发后固定同一连接和批准 IP。
4. 多步工具决策或文件输出后续步骤必须重新检查当前策略；漂移时失败关闭。
5. Provider 只获得既有安全模型输入和公开 Tool Schema，不获得工具凭据或本地权限。
6. Runtime 继续执行 MCP 工具、权限和文件渲染；控制面不复制执行器。
7. flag 关闭、策略为 `legacy` 或模型不在稳定白名单时保持旧路径。
8. Direct Chat 只接入已连接且无需审批的只读 Catalog 工具，不扩大 Workflow/Agent 工具集。
9. 文件输出存储未进入 `shadow` 或 `native` 时必须在能力投影阶段失败关闭。

## 5. 验收

- 能力路由、资格隔离、IP pinning、策略漂移、同目标多步和脱敏测试通过。
- 旧工具、文件输出、R5B 文本、R5C Auto、Canary 与多模态回归通过。
- 前端全量测试、typecheck、生产构建通过。
- 后端全量测试、Compose 配置、Diff、Git 状态和敏感信息扫描完成。
- 独立预览器显示 R5D 状态；真实能力认证和实际工具/文件调用仅在逐次额度授权后执行。

### 独立预览验收证据（2026-08-22）

- 自动门禁通过：R5D 工具/文件专项 59 项、受影响后端 172 项、前端 526 项、
  typecheck、production build、Compose 配置和敏感信息扫描。后端全量为
  3925 passed、29 skipped、20 个已知环境失败；失败仅来自 Agency Worker
  构建产物缺失、Expert Team Agency 及 Skill 跨语言索引环境，不涉及 R5D。
- `chat_tools` 真实调用通过：OpenRouter 单一尝试、实际模型与请求模型一致，
  444 tokens，E2E 3699.48 ms；Catalog 工具调用继续经过只读、无需审批、
  非敏感策略门禁，且后续模型步骤保持同一 Managed Target。
- 文件输出首次调用在 Provider 派发后因预览仍使用 legacy 文件存储而失败；
  根因修复为能力投影在存储模式不是 `shadow` 或 `native` 时提前失败关闭，
  并将独立预览改为专属 native 文件卷。
- 修复后的 `chat_file_output` 真实复测通过：OpenRouter 单一尝试、实际模型与
  请求模型一致，587 tokens，E2E 2418.21 ms；生成 19-byte 文件，SHA-256 为
  `2961d59aff0ac8696e8cb90757a96da422e84c6bbbb01f72bba55e16638bef7d`。
- 最近验收窗口只有一个 `/api/chat` 请求；Router SQLite 和服务日志均未出现
  用户提示、模型回复或文件正文。文件卷仅保存用户明确请求的生成产物。

## 6. 停止与回退

- 若出现派发后跨 Provider 回退、重复执行工具、未授权真实调用、正文落入 Receipt、
  专用模态回归或需要新增依赖，立即停止。
- 回退代码并设置 `MODEL_CONTROL_CHAT_ENABLED=false` 即恢复旧静态路径。
- 保留 v15 表、Provider 凭据、newAPI 数据和现有 Receipt，不执行降级或删除。
