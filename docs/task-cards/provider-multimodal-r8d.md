# 任务卡：R8D Chat Audio 与音频生成 Provider 控制面

## 范围

- 原实施基线：`origin/main@62b60cb9a78186515852e0b0fdb5fbcb3e1889f6`，已包含合并后的 R8C。
- 全量验证基线：`origin/main@cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2`。
- 最终 rebase 基线：`origin/main@0ad5aa9f7e849e1874999f0a25471d331285b3f3`。
- 接管 `chat_audio_input`、`chat_audio_output` 与 `audio_generation`。
- 执行形态分别为 `chat_audio_input`、`chat_audio_output` 与 `audio_generation_stream`。
- 只统一精确 Binding、Adapter、资格、安全出口、一次派发、Receipt、重启恢复和失败边界。
- Chat SSE 与异步音频任务继续保持各自协议；不迁移 STT/TTS、视频、Realtime、Voice Cloning 或 R9。

## 安全边界

- Feature Flag 默认关闭；Policy 为 `legacy` 时原路径保持不变。
- 音频输入、原生音频输出和音频生成必须分别认证，资格不得互相继承。
- 当前计划没有定义组合执行形态；Managed 请求同时要求音频输入和原生音频输出时在 POST 前阻断。
- Managed 调用只允许一个已批准 IP、一个连接、一个模型、一个 Adapter 和一个 Provider POST。
- POST 派发后不得切换 Provider、回退 legacy 或自动重放；中断、超时或重启后的未知结果记为
  `uncertain`，同一逻辑键不得再次派发。
- Chat Audio 只有在观察到精确实际模型、形态所需内容和安全终止后才成功。纯音频输出是合法结果，
  但纯文本不能冒充音频输出成功。
- 音频生成只有在完整 SSE 终止、精确实际模型和完整 MP3 结构均验证通过后才成功；部分音频不得发布。
- 控制面、Receipt、SQLite、日志和管理 API 不保存用户音频、Prompt、模型音频、转录、凭据或完整错误体。

## 验收

- 固定合成 WAV 的 Chat Audio Input、固定短文本的 Chat Audio Output 和固定短 Prompt 的音频生成
  资格分别通过，并且每个形态最多一个付费 POST。
- Chat Audio 的文本/音频 delta、Route Receipt、`message_end` 与 `[DONE]` 顺序保持兼容；缺少实际模型、
  缺少所需内容、非法 SSE、空流、断流、取消和超时均不产生成功 Receipt。
- 音频任务的幂等键最多产生一个 POST；删除内容、页面重复提交和 Server 重启不能擦除派发证据或重放。
- 已派发任务重启后保持 `provider_dispatch_state=uncertain`、`retry_allowed=false`；未派发任务可确定失败。
- Receipt、任务、精确 Binding、实际模型、Adapter 和真实 POST 数一致，且 R5—R8C 与 legacy 路径无回归。
- 独立预览、真实资格与用户入口 Smoke 分别授权；本任务卡及 PR 授权不批准新的付费调用。

## 回滚

- 显式停用受影响 Policy，关闭 R8D Feature Flag 并重启，恢复 legacy 路径。
- 保留 v18 表、资格、Receipt、任务幂等证据和临时媒体生命周期记录；不得删除 Router SQLite、
  Provider 凭据、newAPI 数据或已派发任务证据。

## Help Center Impact

- 影响用户体验：是。Settings 增加三个执行形态的资格与 Binding；Chat Audio 和音频生成在 Managed
  模式下新增派发前阻断、脱敏 Receipt 与不确定结果提示。
- 受影响入口：`/settings?section=providers`、`/settings?section=routing`、支持 Chat Audio 的聊天页与
  音频生成工作区。
- 最新主线的“功能暂不可用时怎么办”保持面向普通用户的通用指南，不加入 R8D 运维细节；具体状态、
  阻断原因和 Receipt 由 Settings 与用户入口就地说明，架构和恢复边界由本任务卡及部署文档承载。

## 当前验收状态

- 全量验证基线上的六个受影响后端文件为 `148 passed, 4 warnings`，R8B/R8C 回归为
  `104 passed, 4 warnings`，主线交叉语义测试为 `154 passed, 4 warnings`；取消、SSE 注入、
  HTTP 错误体有界处理、写盘竞态和 `delivery_pending` 序列化阻塞均已补测。
- 全量验证基线的全量后端为 `5499 passed, 20 failed, 29 skipped`；20 个失败已在同 SHA detached
  基线精确复现，均来自测试镜像缺少 Worker 构建产物或 TypeScript loader 依赖，未发现 R8D 新增失败。
- 前端全量 `893 passed`、Header `1 passed`、production build 通过。标准 typecheck 受 Windows
  `TS5033/EPERM` 增量缓存写入阻塞，app/node 两套非增量等价检查通过。Core、newAPI、Overlay
  三套 Compose 配置验证通过。
- 最终 rebase 后，R8D 与 R8B/R8C 后端专项合并运行 `252 passed, 4 warnings`，Workflow retry
  交叉专项 `99 passed, 4 warnings`，前端 R8D 与 Workflow retry 合并专项 `94 passed`；标准
  typecheck 仍仅出现同一 `TS5033/EPERM`，两套非增量等价检查和 production build 均通过。
- 隔离预览镜像构建、server/client liveness、管理会话配对、三个 Settings 页签、Marble 独立
  可用性和本地音频目录只读探针通过。R8D 三个入口保持 `legacy` 且无 Binding，配对后仍为
  0 连接、0认证、0运行和 0 Provider Call；预览验收不等于真实 Provider 资格。
- 尚未完成另行授权的真实付费资格与用户入口 Smoke；不得据此宣称 R8D 已生产启用或 Provider
  已通过真实验收。
- 所有 R8D Feature Flag 继续保持默认关闭，合并 PR 不等于启用 `managed_required`。
