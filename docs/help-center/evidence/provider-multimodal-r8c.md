# R8C STT/TTS Provider 控制面帮助与预览证据

- 原始实现与真实预览基线：`origin/main@8f9b6f6bd920197519e26434548895f86587e5ec`，2026-08-30。
- Help 重放与完整自动回归基线：`origin/main@ae284fbbbd59831ccdf2df2b34c9cb1239a57220`，
  2026-08-31。PR 提交前又无冲突 rebase 到 `origin/main@03fcaeb60ff41a6b978612b5a25db6ddc6cc86c6`；
  新增上游只涉及 MCP 文档与集成脚本，与 R8C 文件无交集，rebase 后重跑针对性门禁且未重跑付费调用。
- 分支：`codex/provider-multimodal-speech-r8c`。
- 用户入口：Provider 与 Catalog、路由与实验、独立 STT/TTS 工作区、Xpert Chat 音频入口。
- 本文只记录 R8C 的语音转写与语音合成；Chat Audio、音频生成、视频、Realtime 和生产启用未覆盖。

## 自动验证

- 最终 PR 基线上的 R8C 专项保持 `91 passed`；R8C 与 TTS API 针对性复跑为 `134 passed`，覆盖
  Managed TTS 不外发上游 Generation ID且 legacy 响应头兼容。受影响后端整组共 239 项；高并发
  Docker 环境中的首次整组为 `238 passed, 1 failed`，唯一失败是 flat-container import 子进程触发
  固定 30 秒超时；同一失败用例未改阈值、未改源码即隔离复跑 `1 passed`。其余覆盖独立重启用例、
  父认证与多模态会话的原子终态、
  Server 重启后的 split-state 归一化、OpenRouter Generation Metadata 有界轮询，以及 HTTPX
  Generation URL 日志脱敏；其余只有既有 FastAPI `on_event` 弃用警告。
- 最终 PR 基线上的前端全量实际运行结果为 `128` 个测试文件通过、`2` 个失败，
  `857 passed, 3 failed`。两项持续失败均位于未由 R8C 修改的 `models.refresh.test.ts`：当前静态快照
  把 `moonshotai/kimi-k2.5` 计为过期，令 live 数少 1。相同依赖下在 detached
  `origin/main@ae284fbb` 精确复现 `38 passed, 2 failed`，因此分类为基线快照漂移。R8C 新增及
  受影响前端测试 `66 passed`。第三项是无代码交集的 `McpHubPanel` 过期计时用例在全量负载下多出
  一次轮询；随后在当前分支及精确主线分别隔离复跑，均为 `17 passed`，分类为时序噪声而非 R8C
  回归。
- PR 前独立证伪审查发现 Xpert STT/TTS 的旧异步结果可能在切换会话后串入新会话，并发现
  `audio.play()` 直接拒绝时 Blob URL 未释放。最终实现把 Xpert、版本、会话和请求代际共同绑定，
  将 STT/TTS 忙态纳入导航和发送互斥，并在会话变化、播放结束、播放错误或 `play()` 拒绝时幂等
  清理播放资源。终态请求在异步回调前同步认领，兼容旧的一轮一次回调并向 Xpert 逐任务通知；
  deferred callback、失败去重、旧 finally 与 Blob 回收回归均包含在受影响前端 `66 passed` 中。
- 宿主机官方 `npm.cmd run typecheck` 因不能写入 `node_modules/.tmp/*.tsbuildinfo` 返回 EPERM；
  `tsconfig.app.json` 与 `tsconfig.node.json` 的 `--noEmit --incremental false` 完整类型检查均通过。
  Vite 默认配置加载同样命中 Windows `.vite-temp` EPERM；使用官方 `--configLoader runner` 在独立
  临时输出目录完成生产构建（3168 modules），仅保留既有 chunk-size 提示。Node header test `1 passed`。
- 后端全量：`5330 passed, 29 skipped, 20 failed, 10 errors`。20 个失败位于 Agency Worker、
  Expert Team 与容器 TypeScript loader；干净主线同组测试复现更大的既有失败集合。10 个
  World Generation teardown 错误来自只读工作目录，改用独立可写 Store 后 `13 passed`。
- Core、独立 newAPI 与 Overlay Compose 配置通过；`git diff --check` 通过。

## 帮助中心影响

- 更新“功能暂不可用时怎么办”，解释 STT/TTS 的 scope、Adapter、精确资格、Binding 和
  Feature Flag 关系。
- OpenRouter STT 已派发但实际模型证据待确认时，只允许显式“只读刷新模型证据”。该操作只执行
  一个元数据 GET，不重新提交音频，也不产生第二次模型 POST。
- Managed STT 只接收认证格式；Managed TTS 只提供认证声线和外部输出格式。参数不匹配时在
  付费请求前阻断。
- 2026-08-31 在最终独立预览的新标签页中按文章重放了模型市场图片替代路径：选择“图片”输入与
  “图片识别”任务，确认存在“立即面试”替代结果；进入首个聊天页后确认模型入口、输入框与
  发送按钮并停在发送前。文章中的 Provider、路由、费用与数据检查链接均可达，RAG 与 Skill
  入口也能打开。整个重放没有上传文件、发送消息或调用模型。
- Help-only 重放没有再次执行 Provider 资格/Binding、独立 STT/TTS 或 Xpert STT/TTS 四个入口；
  这些入口的结果来自前述另行授权的真实 Smoke，不将其表述为帮助文章自身的操作重放证据。
- Server 重启后 Provider 与路由页按设计显示管理员重新配对入口；本次只读重放未读取或代填
  配对密钥。用户完成配对后，在同一最终预览中复核资格状态与原截图视觉一致。

## 独立预览与真实 Provider 证据

- 当前源码已使用 2026-08-31 最终镜像恢复独立 R8C 预览；Server 与 Client 分别使用 `18153`
  和 `15153`，旧停止容器保留为可回退备份。Router SQLite 复用原独立持久化目录，
  `integrity_check=ok`、Schema v18、WAL，重启后无遗留 `running` 认证或认证会话。
- OpenRouter STT 与 TTS 资格各执行一次真实付费 POST；两者最终均为 `passed`、实际模型与
  请求模型一致。实际模型证据各通过一次显式只读 Generation Metadata GET确认，没有再次提交
  音频或朗读文本。
- 四个用户入口首轮各执行一次授权 Smoke：独立 TTS 与 Xpert TTS 通过；独立 STT 在修复前因
  Generation Metadata 尚未异步可见而误判为 `provider_multimodal_actual_model_unverified`；
  Xpert STT 的 Provider 调用超时并保留为 `uncertain`。
- 2026-08-31 在最终镜像上另行授权独立 STT 与 Xpert STT 各一次真实 POST。两次上游转写均完成，
  但当时的有界实际模型证据解析均未取得结果，两个用户入口均以旧的统一错误码
  `provider_multimodal_actual_model_unverified` 返回 HTTP 502。旧 Receipt 未记录 Generation ID 是否出现、
  只读 GET 次数或等待时长，因此不能把“响应未带 Generation Header”和“当时的 8 秒窗口内 Metadata
  尚未可见”混为已证实的单一根因。两次均未自动重试、切换连接、模型、IP、Adapter 或 legacy。
- 经产品确认，当前实现使用 30 秒总 deadline 内最多十次只读 GET、单次最多 2 秒，并将“缺少
  Generation ID”和“Metadata 等待耗尽”拆为不同稳定错误码。Receipt 只保存 ID 是否出现、实际 GET
  派发次数和等待毫秒数，不保存原始 ID。自动证伪测试覆盖缺少 ID、等待耗尽、单次 GET 超时恢复、
  出口漂移、取消和幂等重放阻断；实际模型验证保持失败关闭。
- 修复后的最终真实重测经单独授权仅执行两个新 POST。独立 STT 与 Xpert STT 均返回 200，页面分别
  显示一次 Managed Provider 调用；两条 Receipt 均为 `passed/confirmed`，请求和实际模型均为
  `openai/whisper-1`。两条记录均观察到 Generation ID，各派发 5 次只读 GET，等待约 10.669 秒与
  9.320 秒后取得实际模型。两个入口各只新增一个 dispatched Call，未重试、回退或调用第二 Provider；
  Xpert 转录文本只写入输入框，未点击“发送”，因此没有伴随 Chat 调用。
- 严格日志审计在这两次重测后发现 HTTPX INFO 请求行曾把 Generation Metadata 的原始 `id` 写入当前
  预览容器日志；SQLite、管理 API 与用户响应未保存该值。该问题已通过窄日志过滤器修复：只把
  `/generation` 请求的 `id` 替换为 `[redacted]`，保留方法、路径、状态码和其他请求日志。真实 HTTPX
  `AsyncClient` 日志回归 `2 passed`，且未再次发起付费调用。修复前的两条历史预览日志不伪装为已清除；
  它们是非凭据 opaque ID，但仍按违反日志最小化门禁处理并记录在本证据中。
- 最终 Receipt 审计显示所有用户 Smoke 的每个父 Run 均只有一个 dispatched Call，多 Call Run、fallback、
  retry 或 replay 均为零。POST 次数结论来自 SQLite Receipt、页面调用计数与容器访问日志的一致结果，
  不冒充网络抓包证明。
- 2026-08-31 在同一最终预览复核视觉状态一致后，沿用原 `842 × 230` PNG 像素资产并迁移到
  最终基线路径；该动作不是重新截图。图片只展示通过的音频资格与 Adapter，不包含凭据、Token、
  内部地址、用户音频、转录或朗读正文：
  `/help-center/ae284fbb/provider-audio-certification-evidence.png`。

## 回滚

- 显式停用受影响 Policy，关闭对应 R8C Feature Flag并重启，恢复 legacy。
- 保留 v18 表、资格、Receipt、Router SQLite 和 Provider 凭据，不删除媒体或 newAPI 数据。
