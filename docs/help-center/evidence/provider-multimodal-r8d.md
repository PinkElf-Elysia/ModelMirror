# R8D Chat Audio 与音频生成控制面证据

- 原实施基线：`origin/main@62b60cb9a78186515852e0b0fdb5fbcb3e1889f6`，已包含 R8C。
- 全量验证基线：`origin/main@cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2`。
- 最终 rebase 基线：`origin/main@0ad5aa9f7e849e1874999f0a25471d331285b3f3`。
- 分支：`codex/provider-multimodal-audio-r8d`。
- 范围：Chat Audio Input、Chat Audio Output 与异步音频生成；不包含 STT/TTS、视频、Realtime、
  Voice Cloning、R9、多租户或计费。

## 当前自动证据

- 全量验证基线上的 R8D 六个受影响后端测试文件为 `148 passed, 4 warnings`；R8B/R8C 回归为
  `104 passed, 4 warnings`；主线 `server/main.py` 交叉语义测试为 `154 passed, 4 warnings`。
  Chat Audio 的 ASGI 2.3 disconnect、ASGI 2.4 send failure、`delivery_pending` Receipt 序列化、
  HTTP 错误体有界处理和重启收敛均有专项覆盖。
- 严格证伪覆盖保留事件注入、顶层错误、非法或截断 MP3、尾部读取错误、重复取消和写盘竞态。
  音频输入附件在断连后仍可重试；失败不会固化成功 Receipt、发布 `output_file` 或自动重放。
- 全量验证基线的后端全量结果为 `5499 passed, 20 failed, 29 skipped`。20 个失败已在同一 SHA 的
  detached 基线 Worktree 精确复现：Agency Worker 14 个、Expert Team 3 个、Skill 3 个，均由
  测试镜像缺少 Worker 构建产物或 TypeScript loader 依赖导致；未发现 R8D 新增全量失败。
- 前端全量为 `893 passed`，Header 测试为 `1 passed`。标准 `npm.cmd run typecheck` 受 Windows
  `TS5033/EPERM` 写入增量缓存阻塞；app/node 两套 `--noEmit --incremental false` 等价检查通过，
  `npm.cmd run build` 通过。
- 最终 rebase 后，R8D 与 R8B/R8C 的后端专项合并运行 `252 passed, 4 warnings`，Workflow retry
  交叉专项 `99 passed, 4 warnings`，前端 R8D 与 Workflow retry 合并专项 `94 passed`；标准
  typecheck 仍仅出现同一 `TS5033/EPERM`，两套非增量等价检查和 production build 均通过。
- Core、独立 newAPI 和 Overlay 三套 Compose `config --quiet` 均通过。隔离预览镜像构建、
  server/client liveness、管理会话配对、三个 Settings 页签、Marble 独立可用性和本地音频目录
  只读探针通过。R8D 三个入口均显示部署开关开启、数据面已接入、Policy 为 `legacy` 且无 Binding；
  配对后 SQLite 仍为 0 连接、0 认证、0运行和 0 Provider Call。以上证据不替代真实 Provider 资格。

## 运行与安全边界

- `MODEL_CONTROL_CHAT_AUDIO_ENABLED` 与 `MODEL_CONTROL_AUDIO_GENERATION_ENABLED` 默认关闭。
  Feature Flag 和入口 `managed_required` Policy 必须同时满足；Policy 为 `legacy` 时保持旧路径。
- Chat Audio Input、Chat Audio Output 与 `audio_generation_stream` 资格相互独立。当前没有组合
  Managed shape，同时要求音频输入与原生音频输出会在 Provider POST 前阻断。
- 音频生成要求 `Idempotency-Key`；同一逻辑键最多一个 Provider POST。派发后的超时、断流、
  取消或重启记为 `uncertain`，禁止自动重放及切换第二 IP、连接、模型、Adapter 或 legacy。
- Chat SSE 和异步音频任务继续使用各自协议。控制面不得保存用户音频、Prompt、模型音频、
  转录、凭据或完整上游错误体。

## 帮助中心影响

- 最新主线已把“功能暂不可用时怎么办”收敛为普通用户指南；R8D 不把旧版运维细节重新写回该文。
- Settings 与用户入口通过就地状态、阻断原因和 Receipt 提示表达 R8D 行为；运维边界保留在本证据、
  任务卡、Architecture 与 Deployment 文档中。
- 不把 R8C 截图或历史授权表述为 R8D 的真实 Smoke 证据。

## 真实 Provider 证据

- 尚未执行 R8D 的真实付费资格或用户入口 Smoke。任何 Chat Audio Input、Chat Audio Output、
  音频生成资格和用户入口调用都需要逐项、逐次授权。
- 在上述证据完成前，不得宣称 R8D 已达到真实验收门禁、已生产启用或某 Provider 已具备相应资格。

## 回滚

- 显式停用受影响 Policy，关闭对应 R8D Feature Flag并重启，恢复 legacy 路径。
- 保留 v18 表、资格、Receipt、任务幂等证据和临时媒体生命周期记录；不得删除 Router SQLite、
  Provider 凭据、newAPI 数据或已派发任务证据。
