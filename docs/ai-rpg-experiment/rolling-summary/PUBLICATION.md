# M2 PR 发布复核

用户本轮“条件PR”按上下文作为“提交 PR”授权，已在执行前说明。Commit、Push、创建 PR 已授权；合并、共享部署及新模型调用未授权。

## 基线与范围

原验收工作区、会话及账本继续保留在 `C:/tmp/modelmirror-rpg-rolling-summary`。源提交 `ce4d13c2a90fa720c527e9f4291628917735dcda` 包含此前逐批验收的 54 个文件；这是已批准 B0–B5 的汇总提交，不是一个未验收的大批实现。

PR 工作区 `C:/tmp/modelmirror-rpg-rolling-summary-pr`，分支 `codex/rpg-rolling-summary-pr`，从最新主线 `c37f1b1a` 建立并 cherry-pick 为 `57b93945`。主线比原基线新增 2 个提交，22 个变更路径与 M2 无同文件交集；核心 RPG 源码逐字相同，运行 hash 仍为 `aaca2ad2796eaec07235d85b989358cc3fd64fdf3745a57e4e512587b67d14b9`。旧验收版本未迁移、未重启。

## 最新主线验证

- 宿主/插件/地球卡：183 通过、0 失败、1 项既有私有样例缺失跳过。
- 受影响前端/帮助：6 文件、53 通过。
- 卡片与 Studio 生产构建通过；Studio 既有大 chunk 警告保留。
- 新独立离线入口 18477（后台 18475）、新数据目录；未配置真实 Provider，真实预算为 0。依据既有帮助从空虚构会话重放安装、明确授权、选摘要模型、设置保存不生成、三回合/首次摘要、Esc 放弃编辑、人工修订、后台时机、第四回合和刷新。4 剧情＋3 摘要均为离线样例，不能作新模型证据。
- 桌面和390px手机截图由正式浏览器工具采集；手机 iframe clientWidth=scrollWidth=375。截图在 `client/public/help-center/c37f1b1a/rpg-rolling-summary-*.png`，没有凭据或真实用户资料。
- 帮助文章仍为 `use-rpg-rolling-summary.md`，更新最新基线、截图及既有真实验收状态；原 `87431327` 两张截图移至 `docs/help-center/evidence/rpg-rolling-summary/87431327/`，B0–B5 记录保留为历史。

完整命令/日志和原始截图存于本工作区 `.rpg04-work/rolling-summary-pr/`；发布仅包含源码、必要帮助图片、合同及摘要登记，不提交会话、Provider 原文、账本、凭据、node_modules、dist 或日志。DELIVERY 区分本次交付与原工作区历史证据。

## 检查中出现的情况

初次暂存检查将冻结 CRLF 当作行尾空白；保留原始字节，使用 `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check` 后通过，没有关闭其他空白检查。临时测试运行器首次有 Windows 路径转义语法错误，修正后相同四组检查通过；状态登记首次默认 GBK 读取失败，显式 UTF-8 后完成。授权插件会自动打开设置，额外的“设置”定位未命中，读取实际对话框后继续。以上均无真实调用。

原提交完成后 Git 自动维护耗时较长，未中断维护；创建工作区前核对目标不存在，单独创建后原串行命令因相同分支已存在而报告失败；已存在的发布工作区不删除或重复创建。后续提交仅对单次命令关闭自动维护，不改仓库配置。

## 实测与回退边界

原授权共5次真实派发：3剧情＋2摘要（首次＋1次增量），全部人工通过，剩余0；本次没有调用。真实后台、更长滚动、人工编辑后的真实承接、第二版摘要之后的第四剧情和旧宿主副本降级未运行，不能宣称这些内容效果通过。CI状态以GitHub实际结果为准。

回退先停用自动总结，保留完整历史/摘要/分支/账本；原工作区提供收尾前源文件与构建副本。旧宿主降级须另用副本验证。PR不启用共享实例，不合并，不部署。

## 帮助图片专项检查

`node client/scripts/verify-help-images.mjs` 初次发现本轮 JPEG/嵌套目录与检查器要求不符。保留浏览器原始 JPEG，导出桌面960px、手机780px PNG；手机使用256色PNG压缩以满足250KB限制，正文和界面不重绘。旧图片归档而非删除证据。所有 M2 图片问题已消除。

用 `git show origin/main:<path>` 导出主线帮助文件原字节到临时副本，执行同一检查器，主线和最终候选均只报告相同3项旧残留：`b266b870/studio-beta-headings.jpg`、`eeb5bbd2/creator-cache.jpg`、`eeb5bbd2/studio-layout.jpg`。未修改这些其他模块文件。全局图片检查结果仍为失败，不能写成全绿；M2未新增失败。为清楚保留此门禁状态，本次创建 Draft PR，等待审阅，不自动合并。
