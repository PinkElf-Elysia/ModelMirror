# RPG 沉浸语音首轮合同

基线：d9dd7094bfbab9db3402e308b35c6da09c228de9；分支 codex/rpg-speech-tts。

范围：手动朗读已完成回复，编辑提取文字，实时语音目录与受控参数，生成/播放/下载/历史，按会话保存音色偏好。复用模镜 /api/multimodal/speech 与 Provider 控制面；不新增供应商密钥或模型微调。STT、自动朗读、角色分句配音、克隆服务部署延后。

调用：首轮仅离线/mock 验证，不调用真实计费服务。语音必须安装并启用插件且手动点击生成。服务端在派发前校验授权、目录、参数和来源回合。音频、请求 ID 和状态单独持久化；未知结果不自动重发。启停和生成不更改剧情、记忆、摘要及旧存档的运行指纹。

验收：node --test studio/speech.test.mjs；插件回归；card-replica 前端构建及文本提取/交互检查；独立本机 fixture 预览。测试数据和音频不提交。后续记录实际命令与结果。

回退：关闭 RPG_SPEECH_ENABLED 或停用插件，已有故事和语音历史保留；本轮不修改共享栈，不执行迁移、提交、发布。

## 实现与配置

- RPG 服务启动时设置 `RPG_SPEECH_ENABLED=true` 才装配插件；Compose 默认 false。服务通过现有 `RPG_CONTROL_URL` 访问模镜的音频目录、Provider 工作负载控制和 `/api/multimodal/speech`。不直接访问第三方、不持有新密钥。
- 插件目录安装后，按会话独立启用。覆盖共用聊天页面的地球、神临、行间、古代、修仙、诡秘、斗罗、龙族、金庸、西幻、中世纪；肥鱼创作编辑器和 AI 娘专用聊天尚未接入本轮入口。
- 语音目录使用主仓 `audio/models` 实时数据，不把模型列表中的名字视为调用资格。Managed 路由只用认证音色/格式；Legacy 路由要求 ready、invocable 与 TTS 能力。语速约束与当前主仓 SpeechWorkspace/tts.py 一致。每次生成重新检查能力与会话插件授权。
- 每个请求一个操作 ID，先持久化请求再派发；同 ID 不重复调用。音频上限 25 MiB、生成超时 240 秒，失败和未知结果分开保存，服务重启不会重放。音频哈希损坏拒绝读取。
- 数据位于 RPG_DATA_DIR/speech/<card>/<session>/<operation>/，音色偏好在浏览器本地。停用不清理音频。不更改作者提示词或记忆策略。
- `speech-baseline.fixture.json` 来自未修改的基线副本，仅中性测试档案与原代码哈希。既有文件保持原换行格式。兼容只接受已登记的旧完整描述符，非本轮漂移、setup 或 hash 篡改仍拒绝；不会重写存档。

## 固定角色音色调研（2026-11-13）

首轮完成会话级“模型 + voice ID”绑定，属于可重复选声，不承诺跨模型保持同一音色。后续角色分句需要先由用户标注角色，再给每个角色绑定声音，避免自动猜测把旁白与对话配错。

| 候选 | 不微调的使用方式 | 接入缺口 |
| --- | --- | --- |
| IndexTTS 2 / 2.5 | 单段参考音频零样本克隆；可单独控制情绪向量 | 需要独立推理服务、参考音频资产管理与服务能力契约；本轮不安装权重或部署 |
| CosyVoice | 官方提供 zero_shot / cross_lingual 推理及服务部署示例 | 需单独部署、记录参考音频与对应文本、核验所选模型实际参数 |
| ElevenLabs Instant Voice Cloning | 短样本创建 voice，不训练自定义模型 | 需商用账户的音色创建和授权流程；不能假定当前聚合网关支持账户自定义 voice ID |

官方来源：[IndexTTS](https://github.com/index-tts/index-tts)、[CosyVoice](https://github.com/QwenAudio/CosyVoice)、[ElevenLabs cloning](https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning)。ElevenLabs Professional Voice Cloning 涉及训练，不在候选范围。IndexTTS 当前文档的八维顺序为 happy / angry / sad / afraid / disgusted / melancholic / surprised / calm；截图含 Love 等不同标签，不能据此断言原站后端就是 IndexTTS，也不能直接照搬八个数值。克隆效果、中文长句、成本和延迟都尚未实测。接入时再核对相应模型权重许可与音色素材授权。

## 后续 STT

下一轮优先复用主仓转写目录与受控音频上传接口。用户明确点击录音、确认麦克风权限后转写；结果先放进聊天草稿，用户确认后才发送故事。不得让转写自动触发剧情、总结、记忆调用。

## 验收边界

UI 已使用本机离线 fixture 完成桌面与 390px 操作、分页、固定音色保存、文本编辑、音频生成/解码播放、刷新历史。真实 TTS 调用为 0；没有部署共享栈、提交、推送或创建 PR。浏览器截图可观察，但截图文件保存受当前工具文件权限限制，正式 PR 前仍需补留档截图。

## 已运行检查

- `npm.cmd test --prefix experiments/ai-rpg-engine/card-replica`：83/83 通过。
- `npm.cmd run build --prefix experiments/ai-rpg-engine/card-replica`：类型检查与生产构建通过；已有 mod-library/custom-config 动静态导入提示保留。
- `node --test experiments/ai-rpg-engine/studio/card-generation.test.mjs`：21/21 通过（含六类会话、原始旧存档及插件授权）。
- 语音、文本提取与 catalog/host/http 插件组合：23/23 通过；旧存档兼容专测 2/2 通过。
- 语音 HTTP 专测追加停用后读取、禁止新生成、卸载后不可访问且保留数据：2/2 通过。
- `docker compose -f docker-compose.yml -f docker-compose.rpg.yml config --quiet`、`git diff --check`：通过。
- 最终预览：`http://127.0.0.1:18503/rpg-app/earth/?session=587b36b9-d053-41cd-a500-9b67d632d284`。仅本机测试提示音，服务进程退出后链接失效。已复核第 12 音色刷新后恢复至第 2 页、固定名称与历史保留、390px 无横向溢出。容器入口和直接 Node 入口均通过同一默认关闭的环境开关装配；没有启动或重建任何容器。

- 容器入口语法检查通过；container-host 与旧档兼容组合 3 项通过、1 项 Linux flock 测试在 Windows 跳过。未宣称容器启动验证通过。

## 授权真实验收补充（2026-11-13，Phoenix）

用户随后授权一次中性文本真实 TTS，未授权克隆。本次通过主仓现有路由调用 Qwen Qwen-Audio-3.0-TTS Flash，音色 longanhuan_v3.6、语速 1、MP3；仅 1 次 POST，HTTP 200。28 字中性文本生成 6.456 秒、129,211 字节音频。浏览器播放器成功解码并进入播放；点击下载得到文件，SHA256 与服务器记录一致。

重启独立本机验收服务后恢复同一条历史并再次播放，没有重放生成。首次强制停止本机 Node 进程触发已有插件所有权保护，使用既有 owner-lock 方法验证旧 PID 已终止并显式恢复锁后，历史正常读取；因此不把本次结果表述为崩溃后无人干预恢复。故事存档的完整 JSON 哈希与调用前一致。费用字段未返回实际金额，未把 estimated/unavailable 表述为已结算。

证据在未跟踪的 .rpg04-work/speech/real-acceptance.json、real-call.json 和 real-data/，不提交音频或运行数据。页面：http://127.0.0.1:18513/rpg-app/earth/?session=c8d79be0-7de4-4161-9f1e-93485653be4c 。原“真实调用为 0”仅描述前一阶段离线验收；本补充记录后续授权真实验收。未部署共享栈、未克隆、未提交 PR。

## 语音界面优化（2026-11-13）

参考用户提供的原站沉浸语音截图，优化 speech.tsx / speech.css：音色采用紧凑选择卡片与明确选中标记；高级配置默认折叠，摘要保留语速与格式；正文编辑器优先可见，字数、超长提示和生成操作分区；桌面历史采用列表和播放器双栏，390px 改为上下排列；模型与请求编号收进可展开详情。没有虚构角色头像或不支持的情绪滑杆。

验证：类型检查与生产构建通过；前端 83 项测试通过；浏览器核对桌面、390px 生成与历史界面、无横向溢出、方向键切换标签、超过 4,000 字禁止生成、现有音频可读取。浏览器未记录 error。本轮新增真实调用为 0，使用上一轮音频验收，不修改服务端、调用参数或存档。回退仅需还原本轮 speech.tsx / speech.css 的展示修改，无数据迁移。
