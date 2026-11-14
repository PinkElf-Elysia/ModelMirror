# 独立角色音色库

## 开工契约

- 基线：origin/main `a6563a9b`；独立分支 `codex/rpg-voice-library`，初始工作树干净。STT PR #467 未合入本基线，不修改它。
- 目标：Qwen/CosyVoice 无训练音色注册、持久音色及角色绑定，复用 RPG TTS 播放、下载、历史。模型、凭据和调用预算由主仓 Provider 控制面管理。
- 范围：server/model_router/voice_library*、server/multimodal/voice*、multimodal/api.py、相关离线测试；RPG plugins/studio/card-replica 接线；设置页与帮助。分为每批不超过五文件的实现批次。
- 不修改作者提示词、既有存档或部署配置；不迁移数据库，不安装新依赖。不提交、不部署。新增私有元数据使用现有 router_metadata，不修改表结构。
- 网络与持久化风险：真实服务默认关闭；管理员显式选择已存储连接、允许模型和单调累计调用上限。创建/查询/删除/合成每次外部请求都消耗一次中央预算；失败及未知结果不自动退额或重发。预算是调用次数，不冒充供应商实际金额。
- 先本地/mock 验证，任何真实克隆、查询、删除、试听均须另行确认。
- 验收：Python voice library 单元/API 测试、既有 speech 测试；Node 插件/语音宿主测试；两个前端 typecheck/build；隔离前端模拟验收。
- 回退：保持 `MODEL_CONTROL_VOICE_LIBRARY_ENABLED` 关闭并禁用插件即可停用新入口，不影响预设 TTS 和已有语音历史。音色删除不删除历史音频。

## 云端合同

官方文档核对日期：2026-11-13。
- https://www.alibabacloud.com/help/en/model-studio/voice-clone-design-http-api
- https://www.alibabacloud.com/help/en/model-studio/voice-cloning-user-guide

使用 voice-enrollment create/query/delete 和 SpeechSynthesizer HTTP；目标模型必须一致。上传仅接受服务端可完整验证的 PCM16 WAV，5–60 秒，16kHz 以上，推荐 10–20 秒。原音频只随显式创建请求传输，不写入日志或元数据；存储摘要、音频参数及云端 ID。首版不修改已注册音色，重新创建即新版本。

## 实现与使用

- `server/model_router/voice_library.py`：使用现有租户存储和 Provider 凭据，集中管理可用模型、单调累计调用额度、私有音色与幂等记录。没有新增数据库结构。
- `server/multimodal/voice_cloud.py`、`voice_library.py`、`voice_api.py`：云端适配及受控音色 API。单次操作总超时 120 秒，未知结果不自动重试；下载只接受经过出口检查的阿里云 HTTPS 地址，不转发 API Key。
- `client/src/components/settings/VoiceLibraryControl.tsx`：管理员在设置中的 Provider 区配置路由。真实调用还要求 `MODEL_CONTROL_VOICE_LIBRARY_ENABLED=true`，默认关闭。
- `experiments/ai-rpg-engine/plugins/voice-library.*`：独立 `rpg.voice-library` 插件，按会话启用。由已有 `RPG_SPEECH_ENABLED` 宿主开关开放，与 TTS 共用控制面服务连接。
- `studio/voice-library.mjs`、`card-replica/src/voice-library.tsx`：音色管理和角色绑定；试听与朗读继续使用 `studio/speech.mjs` 和既有音频历史。
- 角色绑定独立于故事、自动总结与记忆宫殿。用户停用插件后仍可读取记录；删除音色不删除已经生成的语音。
- 提交前先保存操作编号，刷新仅读取本地结果。未知请求至少五分钟后才能显式确认已核对；确认不改变原始未知结果、不退额、不重发。没有取得云端 ID 的未知创建，可在保护等待期后显式查询：按创建前缀和模型查找唯一记录，存在歧义则停止；保留原未知记录并附上核对操作编号，不重新上传样本。
- 本地 WAV 试听只增加 `media-src 'self' blob:`；作者内容的隔离页面保持原有策略。

## 验证记录

2026-11-13，首次开发验证全部离线或 mock，无共享栈部署。后续授权真实验收见下节。

| 检查 | 结果 |
| --- | --- |
| Python `test_voice_library.py`、`test_multimodal_tts.py`、`test_oct13_speech.py` | 74 通过；隔离 `--network none` 后端镜像，源代码只读挂载后复制到临时目录运行 |
| Node speech、voice-library、speech-compatibility、output-edit-compatibility、failed-turn-compatibility、generation-parameters | 36 通过 |
| 控制面组件 `VoiceLibraryControl.test.tsx` | 1 通过；仅本地 mock 请求，确认没有触发合成 |
| RPG `npm run build` | 通过，包含类型检查 |
| 主前端 `npm run build` | 通过 |
| 主前端完整 `npm run typecheck` | 7 个既有错误；在干净 a6563a9b 同样复现，涉及 HarryPotter 测试和 shop-client-measurement 声明 |
| 帮助中心内容测试 | 17 通过、2 个既有失败；干净 a6563a9b 同样存在步骤数与旧截图基线问题 |
| 浏览器交互 | 12 秒合成 WAV 在本机转换并可试听；模拟创建、查询、角色绑定、复用 TTS、下载与刷新历史通过；390px 页面无横向溢出 |
| Git 差异检查 | 通过；无凭据、样本音频、运行数据或构建产物进入差异 |

验证期间观察到 Windows 文件读取与原子改名冲突，已对语音结果的**本地落盘**增加有限重试并测试；没有增加任何模型请求重试。

兼容测试保留 a6563a9b 及此前已审查版本的精确指纹映射，仍拒绝存档内容、设定和未审查运行文件漂移；不重写旧存档。

首次开发阶段未验证真实账号与云端调用；后续 Qwen 验收见下节。音色相似度、供应商实际费用、CosyVoice 真实调用和麦克风硬件录音仍未验证。

## 真实验收预检（历史记录，已解除阻塞）

2026-11-13，用户已授权一轮中性样本的创建、查询、绑定、TTS 播放下载及刷新恢复，禁止发送剧情消息。预检未发出任何云端模型请求，未消耗本轮调用预算。

- 当前共享控制面仅保存 OpenRouter 和 Local newAPI 连接，没有本适配器要求的 DashScope 连接。公开音频目录中的 Qwen TTS 走 OpenRouter，不能据此认定音色注册接口已配置。
- 共享服务的音色库 API 返回 404，功能开关关闭；现有 18539 页面仍为模拟预览。未重启或部署共享服务。
- 既有 STT 中性样本为 PCM16 单声道 WAV，22,050 Hz，5.23 秒，符合本地上传格式要求；尚未上传。
- 待在统一控制面配置具备相应模型权限的阿里云 Model Studio 连接后，使用隔离验收服务执行已授权的一轮调用。不将密钥写入验收文件或前端，不自动重试未知请求。

## 授权真实验收结果

2026-11-13（本机时区），用户完成连接保存后，使用该连接的隔离控制面副本和 `qwen-audio-3.1-tts-flash` 完成一轮。没有部署共享服务，没有发送剧情消息；隔离会话保持 0 回合，验收前后剧情 JSON 相同。

- 云端调用严格为创建 1 次、查询 1 次、合成 1 次，中央调用预算 3/3。三个 POST 均收到 HTTP 200；没有追加创建或合成。查询使用一次 `list_voice`，按本次创建前缀和模型找到唯一音色，状态为 OK。
- 上传既有中性样本（5.233 秒、22,050 Hz、PCM16 单声道），创建“中性旁白 · 云端验收”，绑定角色“旁白”。音色状态 ready；预算耗尽后禁止继续调用，不影响读取与播放历史。
- 合成中性文本“今天阳光很好。我们沿着河边慢慢走，听风吹过树叶。”，得到 6.08 秒、24,000 Hz、PCM16 单声道 WAV。浏览器播放至结束，无媒体错误；UI 下载文件与历史音频 SHA-256 一致：`540f2b5e5f48b299713d98a92695e5e2bd6d6514c636c255c6bc7927df3b66a7`。
- 刷新后音色、旁白绑定、生成历史及音频恢复。隔离验收页：`http://127.0.0.1:18541/rpg-app/earth/?session=27e74288-9b38-44f6-9778-ac904db24252`。

本轮暴露并修复三个云端兼容问题：音色 ID 含点号；结果返回旧式 HTTP OSS 地址；WAV 使用流式占位长度。仅对明确的阿里云 OSS 地址升级为 HTTPS，仍执行出口地址检查，不转发凭据；仅对实际观察到的 PCM 头格式、完整 Content-Length 和对齐音频帧修正长度，PCM 数据不变，其他损坏音频仍拒绝。

上述问题发生后恢复了同一次云端结果，保留原失败与未知记录；下载共两次 HTTPS GET，没有重新合成。创建未知记录通过唯一查询结果关联核对；合成结果经原始响应、请求编号及文件摘要核对后，在隔离验收状态中恢复。本轮不是修复后的全新真实调用重跑，修复后完整路径通过包含实际返回格式的离线回归验证。

修复后验证：`python -m pytest server/tests/test_voice_library.py server/tests/test_multimodal_tts.py server/tests/test_oct13_speech.py -q` 共 82 通过（断网容器、只读源码复制到临时可写目录）；Node `studio/speech.test.mjs` 和 `studio/voice-library.test.mjs` 共 4 通过；RPG 前端构建通过。直接在只读源码目录收集完整后端测试曾因运行目录不可写失败，改为临时副本后通过，没有关闭检查。

本机证据位于 Git 忽略目录 `experiments/ai-rpg-engine/.rpg04-work/voice-real/`：`acceptance-report.json`、`story-before.json`、原失败记录以及 `evidence/audio-recovery.json`、原始与修复后的 WAV。凭据仅在隔离控制存储中加密保存；音频、密钥、签名 URL 和运行数据均不进入提交。

网络方面为本次 API 与下载域名及其 CNAME 配置真实 DNS，解决 VPN Fake-IP；没有豁免保留地址或关闭 SSRF 检查。配置修改前保留本机备份。隔离服务可停止以撤销运行环境；本次云端音色保留供验收，未执行删除操作。
