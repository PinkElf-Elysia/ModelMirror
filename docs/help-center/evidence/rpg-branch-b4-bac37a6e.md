# RPG 可选插件帮助与候选封装实查

日期：2026-09-19。独立分支 codex/rpg-plugin-branch-save，接受基线 bac37a6e5691feeda36d1ec9a548d0692652ad59 + B1–B4未提交候选；对应源码与产物以plugins/DELIVERY.json为准。B3获用户“确认”后进入B4。刷新origin/main=a29da8a5，领先基线2提交且App.tsx有文件交集，未混入候选；如以后获PR授权，仍需最新基线整合/重验。

## 范围和入口

- 用户文章：play-rpg-cards（费用/连续游玩/版本限制修订）；新增branch-rpg-story（8步）。目录、模块速查及全文搜索同步。
- 使用独立18435前端、18434服务，新虚构数据目录 `.rpg04-work/plugin-b4-preview/data/`，Provider禁用。原18433预览/数据未停止或清空。
- 文章编写前可见路径已在B3实查；写完使用从未访问过的18435 origin与新数据重放，不复用已有草稿/筛选/插件安装。预置林遥的1轮离线样例只满足教程前置条件，不当作模型效果。
- 本次未重新验收旧RPG05配置向导和真实输出；其历史结论保留。作者文本、世界书和模型配置未变。

## 按教程实际重放

| 步骤 | 可见操作与实际结果 |
|---|---|
| 1 | RPG → 插件市场；分支存档未安装，展示兼容卡片、权限、费用与保留说明 |
| 2 | 安装插件 → 已安装1；切换已安装页签可见，提示还需会话启用 |
| 3 | 进入地球OL → 历史 → 林遥（帮助演示）1回合；正文分支按钮禁用 |
| 4 | 插件 → 授权并启用 → 关闭；正文分支按钮可用 |
| 5 | 填入原对话草稿后点击正文分支，浮层提示截至第1回合 |
| 6 | 命名林遥的新路线 → 创建并进入；继承1回合，插件未启用，未自动生成 |
| 7 | 对话列表显示父路线/来源回合，切回父路线恢复“原对话的未发送草稿。” |
| 8 | 父会话插件 → 停用插件；分支按钮禁用，已保存路线保留 |

B3另已覆盖刷新、Enter/Esc、空名禁用、取消、卸载/重装和390px；B4教程重放不冒充重新覆盖全部角色向导和键盘路径。无付费调用，真实模型衔接/内容质量未运行。

## 图片真实性

截图均由正式浏览器工具在此候选可见页面拍摄。工具输出JPEG，原字节保存在本目录screenshots/；为帮助中心要求无损编码PNG，解码像素逐字节一致，没有重绘、补画、修饰或改变内容。

- rpg-entry-b4-compact-source.jpg SHA256：3acd199fd3680e7ac5da9541ea8a5e57c064347263744dcf676710aea78082ae。PNG790×691、244771bytes。较宽的早期截图原字节保留，但PNG超过250KiB，故改用更小原生视口重拍。
- rpg-create-branch-b4-source.jpg SHA256：8ce7350f105d47bd00790fa5fe6f3779c621054e1fb6d6eb9ae32365d2a28fee。PNG1000×800、105045bytes。
- 两图公开路径为client/public/help-center/bac37a6e/，没有内部地址、凭据或私人角色。
- 旧公开enter-card.jpg原字节迁到screenshots/rpg-enter-card-a7d99925.jpg，保留历史。没有删除其他模块历史图片。

## 自动验证及失败记录

- `npx.cmd vitest run src/content/help-center/helpContent.test.ts src/pages/HelpCenterPage.test.tsx src/pages/RpgPluginsPage.test.tsx src/pages/RpgChatState.test.ts src/components/StudioBetaPanels.test.tsx --configLoader runner --maxWorkers=1 --fileParallelism=false`：63通过。首次帮助目录入口漏项失败（脚本路径错误未写文件）；纠正后同一套通过。没有关闭断言。
- `node --test experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/card-replica/tests/*.test.mjs`：63通过。合计126项离线/mock。
- 父前端TypeScript/生产构建通过；原大chunk警告保留。帮助3个既有文件在差异检查发现CRLF变化后恢复原LF，只留下必要行差异，再次构建验证。
- `node scripts/verify-help-images.mjs`：全站失败。修改前5处（旧RPG JPEG格式/宽度2处，其他模块未引用图3处）；修改后仅剩下述3处。两份新RPG图的引用、真实PNG、尺寸/体积、替代文本和基线归属均通过。不是全站图片门禁通过。
  - b266b870/studio-beta-headings.jpg
  - eeb5bbd2/creator-cache.jpg
  - eeb5bbd2/studio-layout.jpg
- 未清理无关模块资产、未调低检查标准。未来PR必须如实披露该基线失败，或在授权的对应范围处理。

## 镜像与持久化

`docker build -f studio/Dockerfile -t modelmirror-rpg:plugin-b4 .`通过，镜像ID sha256:63eed9fb3c259ca97dd6ba959cfbd3e5ef18d33cdd0f1c25c6c3cdfe9fa3c739。镜像内两卡TypeScript/Vite构建通过。无依赖升级；npm已有esbuild脚本allow-scripts提示保留，构建未受阻。

`docker run --rm --network none --read-only --tmpfs /data:rw,uid=1000,gid=1000,noexec,nosuid --entrypoint node modelmirror-rpg:plugin-b4 studio/plugin-container-smoke.mjs /data/check 05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca`通过。临时容器无端口映射、无用户卷、Provider禁用/预算0。HTTP验证两卡静态资源、安装不启用、首轮离线、分支幂等、卸载/宿主重启后读取与续玩、父路线/快照不变、重装不恢复授权。Provider实际派发0。

镜像16份关键源码/地球产物与工作区hash一致，runtimeHash也一致；排除.env/.local/.rpg04-work。说明与DELIVERY/STATUS收尾时间晚于构建，镜像内历史文档不作为当前交付状态，以外部当前登记为准。镜像源码/实际运行证据已经固定，父前端和帮助另行打包。

回退优先停用插件并保留现有核心读取，新branches目录不能未经验证交给旧宿主。未做用户卷迁移、共享部署、Commit/Push/PR。真实门禁须另行授权新额度；不能因候选封装而宣告初版闭环完成。
