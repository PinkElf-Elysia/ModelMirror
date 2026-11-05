# 预览从手机切回桌面时顶部按钮重叠修复

日期：2026-11-05。用户已接受变量面板 A 组，本批仅修复布局，不新增真实调用。

## 范围与基线

- 独立工作区：`C:\tmp\modelmirror-variable-preview-layout`。
- 分支：`codex/variable-preview-layout`；Git 基线：`6aa5924dd64b706a684ab2fd0dcc6da1d9feeb08`。
- 以 `C:\tmp\modelmirror-variable-livefix` 的已验收工作状态为实际代码基线，逐字节继承该任务的 51 个变更文件；未包含无关的 provider-coverage 审计改动。
- 本批相对上述实际代码基线仅修改 3 个源码文件，并新增本记录。与 Git 基线比较会同时显示前批继承的变更，不能将其全部归属本次修复。
- 保留 18642、18644、18650 的服务、源码、会话及账本。修复验收单独使用 18652，仅配置离线固定回复。

## 复现与原因

在 18650 已保存对话中，将浏览器视口设为 390px，打开顶部“预览”，保持弹层打开后切回桌面。“会话插件／记忆条目／对话与记录”和路线切换按钮叠在同一行。

桌面弹层规则将所有 `.runtime-actions` 强制定位到 `grid-column:2; grid-row:1`。路线导航也复用了这一类名，因此与主要操作组占用同一网格单元。复现时两组容器的矩形均为 x=487.07、y=87.67、宽=578.47、高=34。不是变量数据、模型输出或预算状态造成的故障。

## 修复

| 文件 | 本批改变 |
| --- | --- |
| `experiments/ai-rpg-engine/authoring/joint-ui.mjs` | 仅给主要操作组增加 `runtime-primary-actions` 类，不改变点击行为。 |
| `experiments/ai-rpg-engine/authoring/creator-experience.css` | 桌面首行定位仅针对主要操作组，收窄至会话面板的直接子元素。 |
| `experiments/ai-rpg-engine/authoring/playback-ui.css` | 路线与恢复操作独占整行；长路线按钮在可用宽度内换行，避免挤压和横向溢出。 |

未改持久化格式、API、模型参数、提示词、变量协议或调用额度；未增加依赖。已有弹窗与操作语义保持不变，本批没有新增二级交互。

## 验证

执行目录为 `experiments/ai-rpg-engine`：

```text
node --test authoring/creator-experience.test.mjs authoring/playback-ui.test.mjs authoring/joint-client.test.mjs authoring/model-ui.test.mjs
47 passed, 0 failed, 0 skipped

node --check authoring/joint-ui.mjs
passed

git diff --check
passed
```

浏览器真实操作覆盖：

- 390px 手机打开预览，再保持弹层切至 1280px 桌面。
- 761px、760px、1024px、390px、1280px 连续切换，包括布局断点两侧。
- 长路线名换行；点击分支与原路线后正确切换会话。
- 会话插件弹窗打开、Esc 关闭并返回焦点；持续要求展开、未保存输入取消。
- Esc 关闭预览，恢复桌面嵌入布局；刷新后会话与路线恢复，再打开预览。

归档的 10 组几何检查中，顶部操作无两两重叠，所有启用按钮中心均命中自身，页面没有横向溢出。截图保留实际页面周围环境。完成后已恢复默认视口。

新预览只执行 1 次 `JointFixtureProvider` 固定回复以形成可切换的对话与分支，不连接真实 Provider。不能将其视为新的模型质量验收；真实供应商调用为 0。原 A 组 4 次调用记录保留。

## 入口与证据

- 修复验收：<http://127.0.0.1:18652/?work=work-e73151b683f93ea1ab752eb2fd6e7e149df5397410fc0bc112a64747a1d6aaad>。
- 原入口 18642、18644、18650 均返回 HTTP 200，原监听 PID 分别为 28760、54016、4408，未重启。
- 18650 实际源码基线 1203 个文件与本批开始时 hash 一致；9 份请求／回执／会话／额度核心记录 hash 一致。
- 本地证据目录：`experiments/ai-rpg-engine/.rpg04-work/preview-layout-20261105/`，不纳入版本控制。
- `baseline.json`：继承代码与旧记录 hash。
- `ui-tests.log`：47 项离线测试输出。
- `captures/browser-checks.json`：10 组实际视口、坐标、遮挡检查。
- `captures/before-desktop.png`、`captures/after-desktop.png`、`captures/after-mobile.png`：修复前后截图。
- `port-checks.json`、`verification.json`：旧入口与文件保留核验、源码及图片 hash。

## 回退与未覆盖边界

停止独立 18652 预览即可回到原验收入口；无需迁移数据、回退账本或重启旧服务。此次没有真实调用，也没有 Commit、Push、PR、Merge 或共享部署。仅验证本次布局相关代码及浏览器路径，未重跑全仓库测试或重复 A 组付费验收。
