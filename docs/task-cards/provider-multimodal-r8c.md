# 任务卡：R8C STT 与 TTS Provider 控制面

## 范围

- 最终 PR 基线：最新合并主线 `origin/main@03fcaeb60ff41a6b978612b5a25db6ddc6cc86c6`。
- 原始实现与真实 Provider 预览基线：`origin/main@8f9b6f6bd920197519e26434548895f86587e5ec`；
  Help 重放和完整自动回归在 `ae284fbb` 完成；最终 rebase 新增的上游仅涉及 MCP 文档与集成脚本，
  与 R8C 文件无交集，因此只重跑针对性本地门禁，不重复付费调用。
- 接管 `multimodal_transcription`、`multimodal_speech`、`xpert_transcription` 与 `xpert_speech`。
- 只统一精确 Binding、Adapter、资格、安全出口、幂等派发和 Receipt；转录 JSON 与音频字节协议不合并。
- 不接管 Chat Audio、音频生成、视频、Realtime 或 Voice Cloning。

## 安全边界

- Feature Flag 默认关闭；Policy 为 `legacy` 时原路径保持不变。
- OpenRouter JSON/Base64 STT 与 OpenAI-compatible multipart STT 分开认证。
- OpenRouter TTS 与 OpenAI-compatible TTS 分开认证；资格不能继承到 Chat Audio。
- 固定参数合同只证明认证过的 WAV，或认证过的 TTS 声线、对外格式和上游格式；不得把一次资格
  扩大为未经验证的格式或声线能力矩阵。
- 不得用请求模型代替实际模型证据。OpenRouter Runtime 在付费 POST 返回但实际模型证据尚未可见时，
  可凭同一 `X-Generation-Id` 在 30 秒总时限内执行最多十次只读元数据 GET，单次请求最多 2 秒；
  显式“只读刷新模型证据”每次仍恰好执行一个 GET。两种路径都不得重新提交媒体。其他 Adapter
  缺少可信模型字段或响应头时失败关闭。Receipt 只保存 ID 是否出现、GET 次数和等待毫秒数，
  不保存原始 Generation ID。
- Provider 目录刷新冻结同一连接与凭据快照；OpenRouter 基础、Embedding、STT、TTS 目录合计
  最多四个只读 GET。配置漂移或补充目录失败时不得写入部分 Inventory，也不得污染上一份
  完整成功目录或连接健康。
- Managed 请求要求 `Idempotency-Key`；资格认证与运行时均在 POST 前持久化派发状态，同一逻辑键
  最多一个 Provider POST，派发后不回退，结果不完整时记为 `uncertain` 且重启不重放。
- 控制面不保存音频、转录、朗读文字、凭据或完整上游错误体。

## 验收

- 固定 WAV STT 与短文本 TTS 资格分别验证实际模型、非空结果、格式和音频 Magic。
- Dedicated 与 Xpert 四个入口的 Route Plan、Receipt、Adapter、模型和真实 POST 数一致。
- 401、429、5xx、超时、取消、响应损坏、模型证据缺失或不一致、重复幂等键和配置漂移均失败
  关闭且不重放；派发后取消必须记录为 `uncertain`。
- R5—R8B、legacy STT/TTS、Catalog 数量和提示词选择器无回归。
- 独立预览、真实认证和四个用户入口 Smoke 逐项授权；任务卡本身不批准付费调用。

## 回滚

关闭对应 R8C Feature Flag 并重启，显式停用 Policy，恢复 legacy。保留 v18 表、资格与脱敏 Receipt；
不得删除 Router SQLite、Provider 凭据、媒体数据或 newAPI 数据。

## Help Center Impact

- 影响用户体验：是。Settings 新增 STT/TTS 资格、实际模型证据只读刷新与四个入口 Binding；独立和
  Xpert 音频入口新增 Managed 阻断、参数限制与脱敏 Receipt。
- 受影响任务和入口：`/settings?section=providers`、`/settings?section=routing`、独立 STT/TTS
  工作区与 Xpert Chat 音频入口。
- 帮助更新：更新“功能暂不可用时怎么办”，说明 scope、Adapter、资格、只读证据刷新、Binding、
  Feature Flag 和各执行形态不能互相继承。
- 原始验证基线：`origin/main@8f9b6f6bd920197519e26434548895f86587e5ec`，2026-08-30；
  R8C 分支在该基线上重建独立预览，并于 2026-08-31 使用最终镜像继续复核。最终 PR 已 rebase 到
  `origin/main@ae284fbbbd59831ccdf2df2b34c9cb1239a57220`，并在该基线上重跑本地自动回归；未把旧的
  真实 Provider Smoke 冒充为最终基线上的新付费调用。
- 当前真实边界：STT/TTS 资格、独立与 Xpert 的 STT/TTS 四个入口均已通过。修复后的最终授权重测
  只执行独立 STT 与 Xpert STT 各一个 Provider POST；两者均为 `passed/confirmed`，请求与实际模型
  均为 `openai/whisper-1`。两条 Receipt 均观察到 Generation ID，并各用 5 次只读 Metadata GET 在
  约 10.669 秒与 9.320 秒取得实际模型；每个父 Run 仅一个 dispatched Call，无重放或回退。严格审计
  另发现 HTTPX INFO 曾在预览日志中包含原始 Generation ID；现已只对 `/generation` 的 `id` 做日志
  脱敏，真实 HTTPX 日志测试与 R8C `91 passed` 均通过；受影响后端整组共 239 项，首次高并发运行
  仅 flat-container import 子进程触发固定 30 秒超时（其余 `238 passed`），该用例未改阈值即隔离
  复跑通过。未再次付费调用。
  PR 前证伪另修复了 Xpert 音频异步结果跨会话串台和播放拒绝时 Blob URL 未回收；请求现绑定
  Xpert、版本、会话和请求代际，音频忙态与会话导航/消息发送互斥；通用 Client Tool 终态通知
  同步去重并保留旧回调 cardinality，相关受影响前端 `66 passed`。
  Chat Audio、音频生成、视频、Realtime 与生产启用不属于本 PR。
