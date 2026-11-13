# 哈利波特卡片接入

## 范围与验收

基线 `origin/main@b4080f2c`，独立分支 `codex/rpg-harry-potter`，开工状态干净。复刻用户实际链接中的哈利波特卡面，作者提示词仅机械提取，不改写或复审。

允许：新建 harry-potter-card 卡包，必要的 RPG 故事馆/聊天路由/插件桥登记、Docker 打包登记、测试与帮助文档。禁止：改写其他卡片提示词、共享服务、用户存档、真实凭据和旧卡运行指纹文件。无数据库迁移，无提交/推送/PR/部署/真实模型调用。

按原文与素材冻结、卡面适配、宿主接入、前端登记、验收分批推进。跨层接入总计超过五文件，按每批最多五个逻辑源码文件推进，保持单一目标；素材文件随卡包统一校验。

风险为中等：执行作者 HTML 时保留不透明来源沙箱、CSP 禁止外部连接，草稿桥核对消息来源；会话使用独立目录。验收包括冻结哈希、重复构建、脚本解析、离线两轮与重启、跨卡隔离、原卡面交互和窄屏检查、前端构建。真实接口只用 mock 验证。回退撤销本卡新增入口与路由，保留新卡存档目录，不迁移或删除旧数据。

## 装配与差异

- 采用用户消息实际 href 对应的哈利波特页面，原始 HTML 不改动，保存在 `harry-potter-card/resources/original.html`；生成适配集中在 `build.mjs`。
- DOCX 机械提取 2,825 段（含 863 空段），保留制表、换行和空段，全文 89,475 Unicode 字符。只校验结构与哈希，不重新评审或改写提示词；完整 system 消息由服务端装配，不向卡面暴露。
- 背景 JPG、来信 GIF 与 164 个字体分片保留来源和 SHA-256；五套 Google Fonts 的 OFL 文本保存在 `resources/licenses`。卡面以数据 URL 加载资源，无外部网络依赖。
- 原有背景、羊皮纸、金色边框、蜡封、来信与抽卡动效、六页签、13 部作品、274 位原著人物、12 类能力、两套独立三选天赋池保留。交互文案与折叠说明来自原始页面。
- 适配只增加独立聊天草稿桥、动态字段可访问标签、键盘入口、减少动态效果支持，以及自定义能力/天赋插入 HTML 时的转义。
- 独立 host/transport 与会话目录，不修改其他卡片的冻结运行指纹。登记故事馆、聊天、插件、插画改写与 Docker 环境开关；真实模型默认关闭、额度为零。
- Dockerfile 同时补齐基线已有依赖的 `authoring` 源码目录：原配置缺少聊天构建引用的 comparison/model UI 文件，运行时还缺 comparison-contract 及其依赖；只补打包，不改工作台逻辑。

## 验证结果（2026-11-13）

在独立 worktree `C:/tmp/mm-rpg-harry-potter` 运行：

- 引擎：`node --test --test-concurrency=1 harry-potter-card/harry.test.mjs studio/card-entry.test.mjs studio/archive-plugins.test.mjs plugins/custom-config/custom-config.test.mjs`：17/18 通过；哈利波特 5 项、哈利波特八插件、跨卡 Mod、旧绑定兼容和嵌套资源入口均通过。龙族插画任务批量运行时出现 `unknown` 而非 `complete`，单独 `node --test --test-name-pattern=dragon-raja studio/archive-plugins.test.mjs` 通过。未修改共享插画实现，未将单测通过当成批量全绿；此不稳定结果尚未定位根因。
- 前端：HarryPotterCard、RpgWorkspace、RpgPluginsPage、RpgFreeOpening、HelpCenterPage 五个文件共 130 项通过。帮助内容 18 项中 17 项通过；剩余失败是基线已有 `manage-rpg-history` 的图片 `/fbe65f12/regen.png` 与文章标记 `479cd636` 不符，已用 `git show HEAD:...` 核对；未改该文章。
- `npm.cmd run build`：主前端与 card-replica 均通过；保留已有包体积、动态/静态导入警告；镜像安装旧 UI/card-replica 锁定依赖时各报告 2 项 high 审计告警，本任务未升级依赖，未将其视为已解决。
- `node harry-potter-card/build.mjs --check`：生成文件可重复，冻结提示词、原始页面和全部素材哈希一致；jsdom 检查页签、减少动效、自定义文本转义、课程/咒语和动态字段标签。
- `npm.cmd run verify:help-images`：本次截图通过尺寸、体积、引用与替代文本检查；全局仍有一个既有孤立资产 `479cd636/regen.png`。不删除其他任务资产。
- Docker 镜像 `modelmirror-rpg-harry-potter:local` 构建通过；无网络、非 root、只读根目录、去除 capabilities、临时数据卷容器中，验证卡面、CSP、不公开提示词、拒绝外源/错误内容类型、零回合会话与离线发送。只创建并清理本任务烟测容器，未重建共享服务。
- Compose 用空临时 env 校验通过；未读取真实凭据。`git diff --check` 通过。
- 浏览器：桌面与 390×844 手机视口核对，卡面无横向溢出。作品折叠/入场、原创身份联动、魔杖说明、课程和咒语、两套天赋三选限制、原著搜索纽特、档案带入、刷新保留、离线发送和故事馆恢复均操作验证。控制台无错误。

## 验证边界与回退

全部生成验收使用离线样例或假 provider；真实模型质量、长篇状态连续性、计费接口、真实插画生成未验证。未提交、推送、创建 PR、合并或部署。当前预览为隔离本地服务 `http://127.0.0.1:18564/rpg/harry-potter`，不代表共享运行站点已更新。

卡面内嵌素材约 15.55 MB，换取不透明来源沙箱下完全离线的原字体与动图；可能增加首次加载时间。建卡表单不持久化，带入聊天后的草稿才保留。

回退时撤销本卡入口、宿主登记与新增包，关闭 `RPG_HARRY_POTTER_CHAT_ENABLED`；保留独立存档目录。禁止用清空旧 RPG 数据作为回退方式。

## 后续真实验收

用户随后授权原文提示词下最多 4 次真实调用，已完成 Gemini 3.8 Flash 开局、连续对话和恢复后续写。详情与恢复门禁、输出偏差见 [2026-11-13 真实验收](HARRY_POTTER_REAL_ACCEPTANCE_2026-11-13.md)。上述“未调用真实模型”描述仅指前一阶段复刻验收。


最终主线整合与验收以 [HARRY_POTTER_FINAL_ACCEPTANCE_2026-11-13.md](HARRY_POTTER_FINAL_ACCEPTANCE_2026-11-13.md) 为准；本文中的未提交、旧基线和测试数均为对应历史阶段记录。
