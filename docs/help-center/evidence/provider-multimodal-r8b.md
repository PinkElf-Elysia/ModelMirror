# R8B 图片、Vision 与原生 PDF 独立预览证据

- 基线：`origin/main@be056e994d99fca2dcc158bd42cc71e8ebfc3db7`。
- 分支：`codex/provider-multimodal-image-r8b`。
- 独立预览：前端 `http://127.0.0.1:15152`，后端
  `http://127.0.0.1:18152`；Router 数据位于独立的 R8B 预览目录，未复用 R8A
  或主工作区的可写数据。
- 已验证前后端均返回 HTTP 200，Settings 未配对状态正常，Marble 仍位于 Provider
  控制面之外。
- 公开状态投影确认 R8B 七个入口的部署开关已开启，但 Policy 默认为 `legacy`，因此
  未认证、未绑定时不会产生 Provider 请求；R8C 及后续入口保持关闭。
- 容器启动日志未出现 R8B 路由、迁移或凭据错误。

## 自动验证

- R8B 专项与原生 PDF `/api/chat` 数据面测试：12 passed，其中固定合成 PNG
  必须通过真实解码校验。
- 受影响后端套件：最终修复后 67 passed；原始 `be056e99` 实现基线的全量后端：
  5,072 passed、29 skipped、25 failed。JUnit 结果保存在独立验收目录，未依赖终端
  缓冲区判断终态。
- 干净 `be056e99` 基线 Worktree 使用相同镜像与相同定向命令，精确复现全部新增
  候选失败；因此 R8B 新增失败为 0。25 项由 14 个缺少 Agency Worker 构建产物、
  3 个 Expert Team Worker 依赖失败、3 个容器内 TypeScript 模块加载失败，以及 4 个
  基线文件资产旧断言、1 个受预览镜像 R8 Feature Flag 环境影响的基线断言组成。
- 前端全量：123 个测试文件、768 tests 与 1 个 Node header test 通过；production
  build 通过。原始增量 `typecheck` 因 Worktree 共用 `node_modules/.tmp` 的
  `tsbuildinfo` 被 Windows 拒绝写入而两次阻断；app 与 Vite 配置分别使用
  `--incremental false` 执行同等严格 TypeScript 检查，均以退出码 0 通过。
- Xpert Vision Receipt 投影修复后，Workflow Vision 定向测试 7 passed；Chat、文件、
  RAG 与 R8B 受影响后端集合 67 passed。成功和失败事件均只投影一个脱敏 Receipt
  对象，多页调用重新编号，前端按入口与运行引用去除渐进重复后再合并调用数。
- Core、独立 newAPI 与 Overlay Compose 配置均通过。
- `git diff --check` 通过；生产文件与文档未发现凭据或预览密钥。
- 发布前已 rebase 到 `origin/main@b143cde199feaf3a6304b8680739246b087100b3`。
  最新主线交叉验证为：后端受影响套件 67 passed、前端 Receipt/Settings 定向
  25 passed、app 与 Vite 配置 TypeScript 严格检查均退出码 0；Vite 7.3.5 使用
  `--configLoader runner` 在独立临时目录完成 3,165 modules 的 production build。
  rebase 后未重复运行全量后端或前端全量测试，原始全量结果不冒充最新主线全量结果。

## 真实 Provider 证据与待验收项

- 管理员配对、R8B Adapter 选择、付费确认门禁与失败证据持久化已在独立预览确认。
- 首次 `chat_image_stream` 真实认证只产生一个 OpenRouter Chat POST，上游返回 HTTP
  400，记录为 `provider_workload_http_error`；无重试、第二连接或 legacy 回退。
- 证伪检查发现仓库内固定 1×1 PNG 的 IDAT CRC 无效。已替换为可解码的 2×2 PNG、
  增加回归测试并重建预览。
- 修复后的 `chat_image_stream` 重测只产生一个 OpenRouter Chat POST并返回 HTTP 200；
  资格为 `passed`，实际模型与请求的 `openai/gpt-4o-mini` 一致。上游报告
  `8511` 个输入 token、`3` 个输出 token，共 `8514`；这是一条调用的 usage，不是重复调用。
- SQLite 保留首次失败和修复后通过两条脱敏资格记录；认证表没有 Prompt、消息、图片、
  模型正文、完整响应或 Key 字段。
- `chat_image`、`chat_document_native`、`workflow_interactive_vision`、
  `workflow_deployment_vision`、RAG Vision 与 `image_generation` 的真实资格和用户入口
  Smoke 已逐项完成；每个受控逻辑调用均对应一个 Provider POST，未观察到第二连接、
  第二 IP 或 legacy 回退。
- Published Xpert Smoke 使用公开测试图片，运行 ID
  `53069871-cb29-4780-904d-0fa736d34a4c`、任务 ID
  `a1abefbddf4a475fb81614c3617253b9`。SQLite 只记录两条脱敏、已确认调用：
  `xpert_vision/vision_json_unary` 一次（25,657 tokens）与 `xpert/chat_text` 一次
  （586 tokens）；两者实际模型均与精确绑定一致，无重复和远程回退。
- 严格审计发现成功 Vision Receipt 原先只写入 SQLite 和节点变量，未进入成功
  `node_end`，导致 Xpert 页面最终只显示后续文本调用一次。现已修复成功/失败事件投影
  及前端多阶段合并，并由 Mock 回归证明显示口径为两次且不会重复计算渐进 Receipt。
- 修复后在同一预览构建完成新的真实浏览器闭环，页面运行 ID 为
  `2e2f7f7e-3444-4f30-8f56-4686cd31faa1`：用户侧正确显示调用 1 与调用 2，最终状态
  `completed`。SQLite 同期只新增 `xpert_vision/vision_json_unary` 一次
  （25,635 tokens）和 `xpert/chat_text` 一次（507 tokens）；两条记录均为
  `dispatched=1`、`confirmed`、`passed`，请求与实际模型均为
  `openai/gpt-4o-mini`，无第二连接、远程回退或重复 POST。
- Smoke 期间仅有一个伴随的 `/api/files/outputs` 只读请求返回 503；已确认预览器明确
  关闭 `FILE_OUTPUT_ASSETS_ENABLED` 与 `CHAT_FILE_OUTPUT_TOOL_ENABLED`，该请求不在
  R8B Vision 数据面内，也未影响回答、Receipt 或运行完成状态。
- 本证据仅证明 R8B 已验收的 operation/Adapter；不声称 newAPI 多模态默认切换或 R8C
  及后续能力已经通过。任何新增付费 POST 均需逐次授权。
