# 完蛋！我被 AI 娘包围了！

范围：原卡入场、介绍、八位角色和建档表单复刻；原版提示词机械装配；保留本地背景音乐，移除作者的话和站点悬浮播放器。

基线：b4080f2c。独立分支 codex/rpg-ai-girls-gemini，不修改共享栈或其他工作树。当前为草稿：Gemini 实测未完全通过。

## 来源与再生成

resources/source.json 记录 Word 与提取正文、原始前端的 SHA-256；resources/assets.json 记录 11 张图片和音乐的来源及哈希。system.txt 按 Word 正文段落顺序机械提取，不做语义修订。original.html 保存来源，public/index.html 是定点适配后的页面。

运行 `node experiments/ai-rpg-engine/ai-girls-card/build.mjs` 再生成页面；资源或原文漂移时停止生成。

## 会话与边界

入口 `/rpg/ai-girls`，原卡面板 `/rpg-app/ai-girls/`，独立聊天 `/rpg-app/ai-girls/chat/`。开始游戏只带入草稿，不自动发送。音乐需要用户点击入场后播放，可用页面内音乐按钮暂停。

新卡使用隔离的 archive-chat/ai-girls 存档。为保留其他卡片的运行指纹，沿用肥鱼独立挂载结构，冻结适配的四份 transport 文件，并用 parity 测试防止与受控传输验证分叉。

默认离线演示。真实路由由运营者显式开启 RPG_AI_GIRLS_CHAT_ENABLED=true，设置 RPG_AI_GIRLS_CHAT_CALL_LIMIT，并复用 RPG_CONTROL_URL/RPG_S2S_TOKEN；本任务仅在隔离预览中执行已授权的两次真实调用，不部署共享栈。

## 验收与回退

`node --test experiments/ai-rpg-engine/ai-girls-card/*.test.mjs`

`npm --prefix experiments/ai-rpg-engine/card-replica run build`

`npm --prefix client run build`

另外核对桌面/手机页面、音乐、角色表单、开局草稿及刷新恢复。模型效果未由离线验收证明。

回退时撤销本卡入口和 container-host 挂载，保留 archive-chat/ai-girls 的存档文件；不要覆盖其他卡片运行文件或删除共享数据。

## 本次验收记录（2026-11-13）

- 原 Word 与系统正文哈希复核一致，系统 SHA-256 为 e308eec7f408333768596a9f6de27686716ea74f4508538a0b9bc6c583d95056。
- 新卡与流式展示 Node 测试 20 项通过；五组 RPG 前端回归 98 项通过。
- 帮助内容测试 17 项通过、1 项失败：既有 manage-rpg-history 截图路径与元数据基线不一致；未改旧截图或放宽检查。
- 两套前端构建、Compose 配置、独立 Docker 构建及无网络只读容器冒烟通过。
- 桌面/手机、音乐暂停/继续、建档归纳、独立聊天和草稿刷新恢复已在浏览器核对；五张真实截图随帮助文章交付。
- Gemini 3.8 Flash 严格派发 2 次、重试 0：第一轮完成并保存；第二轮上游 content_filter / PROHIBITED_CONTENT 截断，没有形成完整回合。提示词与续轮历史装配完整，但连续两轮效果验收失败。用户明确授权按此结果提交草稿 PR。
- 未验证长期对话和真实记忆插件；没有追加第三次调用，没有部署共享栈。原始响应和会话只保留在忽略目录，不纳入 PR。

详细证据见仓库 docs/ai-rpg-experiment/AI_GIRLS_CARD.md 与 AI_GIRLS_LIVE_ACCEPTANCE.json。
本地验收入口：http://127.0.0.1:19906/rpg/ai-girls 。预览数据位于忽略目录 .rpg04-work/ai-girls-live/data，与共享栈隔离。

## 提交前主线整合

主线在验收期间合入故事馆统一布局与模型目录更新，本分支整合到 f000baf8。保留统一“全部作品”、搜索、来源筛选和本地作品发布入口；AI 娘计入十一张内置卡片，旧卡及创作助手均保留。原始两次真实调用的实际基线仍为 b4080f2c，不能改写成新基线实测；没有重跑真实调用。新基线仅复验入口、建档、聊天恢复、帮助和离线集成。

原文 system.txt 的四处尾随空格及末尾空段落作为作者原始字节保留；完整 diff --check 因此报告空白告警。代码与其他新增文档的空白检查通过，不为消除告警更改冻结原文。

最新主线截图工具另有 12 处既有问题：故事馆 JPEG 不符合 PNG 校验、发布截图宽度 1280、五份未引用旧资产；本任务五张 PNG 均满足格式、750–1000px 宽度、250KB 以内与引用规则。未修改上游资产或放宽校验。
