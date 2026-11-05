# 变量面板首批

范围：直接补充创作模块的数据骨架、Handlebars 皮肤和随剧情更新的状态。既有特殊 CG 基线保持独立；18642 不替换。新预览使用 18644、独立目录及固定离线回复。不新增插件，不进行真实模型调用。

## 观察事实与本地约定

原站测试作品 d2d3604a-145e-4030-9fb6-f586127995d5 的基础设置已通过浏览器实际操作：使用变量面板开关；JSON 数据骨架；HTML 面板皮肤；变量提示词工具栏；JSON 全屏后 Esc 退出；HTML 全屏按钮退出；关闭隐藏字段，再开启保留内容。没有保存原站草稿，也没有导入原作者图片。模板观察到 Handlebars 条件、列表、比较与 data-panel-copy / data-panel-copy-from。原站未公开的状态更新协议未推断成已确认行为。

本地采用完整快照协议 `<variable_panel>JSON 对象</variable_panel>`，恰好一份。初始数据是 JSON 实例，不是 JSON Schema：初始键必填且类型稳定，null 开放类型，非空数组按首项匹配；初始数组自身不符合首项结构时，预览/发布会阻断并说明，草稿仍可保存；允许新增键，新增键不自动成为后续必填键。模板普通插值转义，支持 if/unless/each/with 和 eq/ne/gt/gte/lt/lte/and/or/not；拒绝局部模板、动态 helper、原型路径。已批准依赖 handlebars@4.7.10，MIT，传递版本由 lockfile 固定；运行绑定包含 lockfile。

## 保存与请求合同

`document.settings.variablePanel` 是可选字段，含 enabled / skeleton / template / instructions。源码字符串原样保存；草稿允许未完成 JSON/模板，启用后的发布和运行要求有效配置。关闭保留源码，不修改既有发布版本或会话。

作品版本定义初始数据和配置 hash；会话绑定定义。每次完成回复与 `turn.variablePanel` 同一原子记录落盘。每个结果含源回复 hash、前状态 hash、状态 revision、dataHash、诊断和整体 hash；校验时根据历史原文重建，不能只重算 hash 篡改状态。分支继承完整历史点快照，不读取父路线未来修订。不迁移旧会话。

本轮 user 的既有前后置词及持续要求后附加变量提示词、稳定序列化当前状态和格式要求，system 原样保留。冻结的请求保存实际面板状态，恢复不换操作 ID、不重新抽样。普通历史 user 不重复携带过去面板要求；AI 原文继续由世界书/M2/M3 读取。面板不增加独立模型调用，不修改插件模型选择、授权或预算。

完整合法更新推进状态；合法相同数据不增加 revision。缺失、重复标签、无效 JSON、重复键、原型键、类型/字段不匹配、超限保留上一状态。失败、截断和未知剧情不创建完成回合；显式恢复原回执后只应用一次。模型内容是否真实仍由人工判断，结构合法不代表内容正确。

## 渲染与接口

POST `/rpg-app/api/authoring/variable-panel/preview`：workId、expectedRevision、config，临时只读预览，不保存、不调用模型。

POST `/rpg-app/api/authoring/joint/playback/panel`：sessionId、turnNumber（0 为初始状态），宿主从固定作品版本及已校验历史决定数据与模板，前端不能传受信状态。

Handlebars 在受限 Worker 中渲染，输出继续走现有 HTML/CSS 清理和资源映射。面板仅授予固定复制桥接，凭来源窗口与随机 token 校验；无模型脚本执行、网络、凭据或宿主存储。复制成功短暂设置 data-copied。模型普通回复不获得面板复制授权。

只有合法变量标签在展示副本中移除；持久历史不变。面板编译失败单独提示，剧情仍可读。异步旧预览释放缓存，不挂到新作品/回合。原始状态可展开查看。

容量：状态 JSON 64 KiB、2000 值节点、16 层；模板 200000 Unicode 字符、受限 AST；提示词 20000 字；模板输出 512 KiB；Worker 48 MiB、默认 1500 ms、最多 4 并行。进一步沿用展示层容量限制。不静默截断、不附加压缩调用。

## 验证与回退

可运行：`node --test authoring/variable-*.test.mjs`、`npm run test:authoring`。测试覆盖原样配置往返、模板隔离、状态原子更新、拒绝保留、未知/重启、分支、M1/M2/M3、零面板路径、HTTP 冻结回放。浏览器验收使用从空白 UI 创建的中性样例。

本批离线范围外：真实模型输出遵循度与内容质量、外部模板未支持 helper、任意 JavaScript、状态手工编辑/回滚及增量 patch 协议。不声明原站未公开内部更新算法等价。

回退：关闭草稿变量面板并发布新版本用于新会话，保留源码、资源、历史与旧版本；需要退宿主时使用数据副本和原验收服务。不得把包含新面板字段的会话直接交给旧宿主。

验证结果和源码/产物 hash 见本地 `.rpg04-work/variable-panel-dev/acceptance.json`。此文件不进入仓库，不包含密钥、原站素材或真实生成数据。

## 本批验收记录（2026-11-05）

独立分支 `codex/creator-variable-panel`，基线 `6aa5924dd64b706a684ab2fd0dcc6da1d9feeb08`。承接已验收的特殊 CG 文件副本，来源 hash 另行冻结；没有改写 `codex/creator-cg-panel` 或 18642。新增/扩展文件按以下责任审阅：

- 配置与状态：`variable-panel.mjs`、`contracts.mjs`；模板解释：`variable-template-syntax.mjs`、`variable-template.mjs`。
- 请求与会话：`memory-context.mjs`、`joint-session.mjs`、`joint-session-state.mjs`、`summary-state.mjs`、`runtime-binding.mjs`。
- 分支：`branch-checkpoint.mjs`、`branch-origin.mjs`、`branch-session.mjs`。
- 展示：`variable-render.mjs`、`presentation.mjs`、`turn-playback.mjs`、`playback-ui.mjs`、`playback-ui.css`。
- 编辑与接口：`variable-panel-ui.mjs`、`variable-panel-ui.css`、`variable-frame-ui.mjs`、`browser.mjs`、`server.mjs`。
- 验证与帮助：三份 `variable-*.test.mjs`、本说明、`creator-guide.html`；生产依赖与 guard hash 分别记录在 package/lockfile 和 `docs/audits/provider-coverage.json`。

实际执行结果：

| 检查 | 结果与范围 |
| --- | --- |
| `npm.cmd run test:authoring` | 883/883 通过；在最后的初始数组边界修正前运行，保留完整日志 |
| `node --test --test-concurrency=4 authoring/variable-*.test.mjs authoring/store.test.mjs authoring/playback-ui.test.mjs` | 最终源码 69/69 通过，含 31 项本批用例；先记录初始数组问题的失败复现，再加入校验后复跑 |
| `scripts/check_provider_coverage.py` | Python 3.12.14，与 CI 版本系列一致；82 个登记项、979 个候选点通过；仅改四个已审阅 JS 文件 hash，不改变分类 |
| `python -B -m unittest discover -s server/tests -p test_provider_coverage.py` | 26/26 通过 |
| `ui` 和仓库 `client` 的 `npm.cmd run build` | 两处构建通过；主 client 仍有既有大 chunk 警告 |
| 浏览器 | 1280×900 与 390×844；开关保留、JSON/HTML 全屏退出、复制、错误提示、保存刷新、版本与固定回复回放通过 |

系统默认 Python 3.13 会产生不同的 AST 指纹，曾触发覆盖审计不匹配；已改用已有的 Python 3.12.14 复核并通过，没有批量改写 Python 指纹。18644 使用纯 `JointFixtureProvider`，无真实路由或密钥。最终样例通过 UI 复制/发布/新建会话，调用一次固定回复；先前样例因源码版本变化而停止兼容，其记录原样保留，没有迁移或换 hash。

验收入口：<http://127.0.0.1:18644/?work=work-4bfdf9ea14459492ab4146f70b953f667185bfa0a033919fdf778792fd1571e3>。基础设置底部编辑变量面板；右侧“实际对话”查看已保存回放。初始体力 80，完成回复后为 70、地点为北岸灯塔、增加铜钥匙并完成任务；刷新保持，草稿初始值仍为 80。最终桌面、手机、编辑同屏截图位于本地 `captures-final/`，与第一次截图分别保留。

此批仅完成离线验收；真实模型是否遵循完整状态协议、更新内容是否忠实仍未验证。没有执行 Commit、Push、PR、合并或共享部署；本机测试作品的发布是离线验收步骤。
