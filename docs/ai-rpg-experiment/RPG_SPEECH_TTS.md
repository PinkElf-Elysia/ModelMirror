# RPG 沉浸语音首轮合同

实现基线：d9dd7094bfbab9db3402e308b35c6da09c228de9。PR 集成基线：fefc14b0c5a020c5031880ab697e60826211fe3c；独立分支 codex/rpg-speech-tts-pr。

范围：手动朗读已完成回复，编辑提取文字，实时语音目录与受控参数，生成/播放/下载/历史，按会话保存音色偏好。复用模镜 /api/multimodal/speech 与 Provider 控制面；不新增供应商密钥或模型微调。STT、自动朗读、角色分句配音、克隆服务部署延后。

调用：先离线/mock 验证，后按单独授权完成一次真实中性文本 TTS；PR 收尾不追加真实调用。语音必须安装并启用插件且手动点击生成。服务端在派发前校验授权、目录、参数和来源回合。音频、请求 ID 和状态单独持久化；未知结果不自动重发。启停和生成不更改剧情、记忆、摘要及旧存档的运行指纹。

验收：node --test studio/speech.test.mjs；插件回归；card-replica 前端构建及文本提取/交互检查；独立本机 fixture 预览。测试数据和音频不提交。后续记录实际命令与结果。

回退：关闭 RPG_SPEECH_ENABLED 或停用插件，已有故事和语音历史保留；本轮不修改共享栈，不执行迁移。用户已授权提交、推送和 PR，未授权合并或部署。

## 实现与配置

- RPG 服务启动时设置 `RPG_SPEECH_ENABLED=true` 才装配插件；Compose 默认 false。服务通过现有 `RPG_CONTROL_URL` 访问模镜的音频目录、Provider 工作负载控制和 `/api/multimodal/speech`。不直接访问第三方、不持有新密钥。
- 插件目录安装后，按会话独立启用。覆盖共用聊天页面的地球、神临、行间、古代、修仙、诡秘、斗罗、龙族、金庸、西幻、中世纪、哈利波特；肥鱼创作编辑器和 AI 娘专用聊天尚未接入本轮入口。
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

UI 已使用本机离线 fixture 完成桌面与 390px 操作、分页、固定音色保存、文本编辑、音频生成/解码播放、刷新历史。真实 TTS 调用为 0；没有部署共享栈、提交、推送或创建 PR。此阶段未保存截图；PR 收尾已在最新主线独立预览补齐，见下方记录。

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

## PR 集成验收（2026-11-13）

- 独立工作树基于 main fefc14b0，保留失败回合恢复和 Windows 宿主所有权恢复；解决聊天页合并遗漏的朗读按钮。
- 新合入的哈利波特共用聊天接入相同可选插件。使用从未修改的 d9dd7094 / fefc14b0 副本生成的中性存档和完整指纹快照做兼容验证；不放宽未知变化，也不重写存档。failed-turn 夹具重建同时投影已登记的语音前文件指纹，仍断言原精确哈希、内容与 setup 篡改拒绝。
- `npm.cmd test --prefix card-replica`：86/86；`npm.cmd run build --prefix card-replica`：类型检查和生产构建通过。两条既有动态/静态导入告警保留。
- Windows speech / compatibility / failed-turn-compatibility / local-owner：10/10。六张参数卡语音与两基线兼容测试通过。首次组合测试的三项指纹失败已修复并定向复跑通过，其余 37 项通过、Linux flock 一项在 Windows 跳过。
- Docker 独立镜像构建通过；断网、只读、非 root、drop ALL、no-new-privileges 临时容器运行 speech / compatibility / container-host：7/7，包括 Linux flock。
- 最新基线帮助实操与截图见 `docs/help-center/RPG_SPEECH_TTS_PREVIEW.md`。本轮 fixture 生成 1 次，真实调用 0 次；上一轮真实中性音频结果保留，不重复收费验证。
- 不运行全量 Python 后端测试；本 PR 未修改主仓 Python 或主聊天 SSE。STT、自动播放、声音克隆、AI 娘与肥鱼专用聊天接入、真实费用核对仍不在本轮完成范围。


## 授权同步 main（2026-11-13）

仅在 PR #464 分支合入 c9db303420883cd3682c6304994c204df788a91f，解决聊天页、插件目录、插件宿主与服务入口四处冲突，保留语音和主线商店各自默认关闭、独立授权及请求边界。未合并 main、未部署、未新增真实调用。

兼容映射新增精确 c9db3034 和更新前 PR 4a32fbe2 快照，并补齐旧 controlled-provider 文件指纹；旧快照来自独立干净 checkout 的中性会话。仍使用原完整校验器，拒绝未知描述符、设置和 hash 篡改，不修改原存档。

验证：
- `node --test studio/speech.test.mjs studio/speech-compatibility.test.mjs studio/failed-turn-compatibility.test.mjs studio/local-owner-lock.test.mjs studio/controlled-provider.test.mjs plugins/catalog.test.mjs plugins/host.test.mjs plugins/generation-parameters.test.mjs harness/*.test.mjs`：首轮 133/134 通过；唯一失败为新增语音后插件总数断言。更新总数为 12，并追加语音默认关闭断言；`node --test plugins/generation-parameters.test.mjs` 22/22 通过。
- `node --test --test-name-pattern="optional speech" studio/card-generation.test.mjs`：通过，覆盖六卡四个精确基线。
- `npm.cmd test --prefix card-replica`：86/86；`npm.cmd run build --prefix card-replica`：类型检查与生产构建通过，既有 mod-library 导入告警保留。
- Docker Compose 配置、独立镜像构建通过。Linux 安全冒烟因本机 Docker daemon 消失未运行，不沿用旧基线通过结论。
- 当前 UI 复验和两张新截图见帮助实操记录；1 次离线提示音，0 次新增真实调用。全量帮助检查既有两项内容失败和 10 项图片问题仍保留，不削弱校验。

回退：如需撤销本次同步，可在 PR 分支另做 revert；不得 reset 已公开历史。关闭语音插件即可阻止新生成，保留既有音频，无数据迁移。
