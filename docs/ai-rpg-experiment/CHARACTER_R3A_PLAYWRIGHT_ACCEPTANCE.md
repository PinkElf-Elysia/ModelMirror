# R3a 独立 Playwright 页面验收

日期：2026-11-14（America/Phoenix）。基线 `2a7dfea1e8475ba2654ee4055e1c4cb544028483`，分支 `codex/rpg-character-r3a`。此文补充 `CHARACTER_R3A_PAGE_ACCEPTANCE.md`，不替换先前受阻记录。

## 结论与范围

**独立真实 Chromium 浏览器验收 16/16 通过。** 原生 JavaScript confirm、beforeunload、实际文档重载、头像真实解码、身份快照和桌面窄屏流程已取得可复核证据。没有确认新的产品缺陷，生产代码、测试断言、运行指纹和项目依赖未修改。本轮只新增本文档；临时测试脚本、工具依赖、会话、日志和截图均在仓库外。

此处“原生对话框通过”指浏览器真实 dialog 事件与接受/取消后的状态，不声称已人工检查 Windows 弹框外观。独立浏览器为 headless Chromium，不是 JSDOM；390×844 是桌面浏览器视口，不是真机。

**仍未运行：Windows 原生文件选择器取消、真机软键盘/触控/系统返回。** FileChooser.setFiles 事件不替代 OS 取消操作，桌面视口不替代手机输入法。

## 隔离与工具

- 用户明确批准独立 Playwright 进程及临时依赖。工具目录 `C:/tmp/mm-r3a-pw`，Playwright **1.65.1 / Apache-2.0**，Chromium **159.0.8154.6**；依赖锁及下载均限临时工具目录，不写项目 package/lock。
- 数据从上一轮中性副本复制到 `C:/tmp/mm-cr3a-data/pw-acceptance-20261114/data`。只排除复制的活跃 `owner.json`，原所有权与 owner-dead 证据不变。
- 预览固定 fixture 模式，loopback **43158 / 43159**。专用浏览器配置在每次 run 的 browser-profile 内，不连接用户 Chrome，不使用用户 cookie 或凭据。
- 浏览器页面请求只允许两个独立 origin；发送/恢复模型请求端点被测试侧拒绝。本轮没有触发该拒绝：blocked=[]、pageerrors=[]。生成请求为零，连固定 fixture 回复也未发送；会话仅创建预设开场。
- 没有数据迁移、真实调用、共享服务部署、提交或推送。

## 逐项验收

| 编号 | 场景 | 结果与证据 |
| --- | --- | --- |
| 01 | 取消档案切换 | 通过：真实 confirm.dismiss 后称呼与中文多行描述保留 |
| 02 | 确认放弃 | 通过：真实 confirm.accept 后新建表单清空 |
| 03 | 取消刷新 | 通过：beforeunload.dismiss，performance.timeOrigin 不变且草稿保留 |
| 04 | 确认刷新 | 通过：beforeunload.accept，timeOrigin 改变，未存输入消失，已保存修订仍在 |
| 05 | 站内离页 | 通过：取消停留原页并保留草稿，确认后进入会话目录 |
| 06 | 关闭标签页 | 通过：close({runBeforeUnload:true}) 取消后仍开放且草稿保留；接受后取得 close 事件，重开只读到已保存数据 |
| 07 | PNG/JPEG/WebP | 通过：真实文件选择事件、图片解码、转换为 512×384 PNG；每次保存后真正重载并重选档案，头像仍可显示 |
| 08 | 损坏头像及同文件重选 | 通过：连续两次选择同一损坏 PNG 均显示中文错误，旧头像引用保留、input 清空；错误后可放弃未存输入 |
| 09 | 原身份创建单聊/群聊 | 通过：各有一条预设开场，没有生成请求 |
| 10 | 移动宽度编辑档案 | 通过：名称与多行描述更新、移除头像、保存新修订，无横向溢出 |
| 11 | 新单聊及刷新 | 通过：新称呼用于开场，完整文档重载后仍只有一条开场 |
| 12 | 群创建草稿刷新 | 通过：真实 beforeunload 接受、timeOrigin 改变后，档案引用、场景和开场选择完全恢复 |
| 13 | 抽屉与输入草稿 | 通过：抽屉显示当前身份，关闭后焦点回到入口；长中文输入在真实重载后保留，不进入正式历史 |
| 14 | 旧会话身份隔离 | 通过：重新打开并重载旧单聊/群聊仍使用旧身份；群成员仍显示旧称呼 |
| 15 | 跨页与布局 | 通过：档案、角色库、单聊、群创建、群聊、目录完成导航；相关页面 scrollWidth <= innerWidth=390 |
| 16 | 请求与页面异常 | 通过：无 send/recover/continue 请求，无意外外部页面请求，无 pageerror |

快照交叉核对：本轮旧身份显示修订 3（内部 2）含头像；新身份显示修订 4（内部 3）头像为空。新旧单聊的 turns=[]、operations={}；对应群聊只有 create 事件，玩家快照分别与单聊完全一致。复制来的两份旧单聊文件哈希及两份旧群聊对象保持不变。

## 可复现命令与本地证据

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH='C:/tmp/mm-r3a-pw/browsers'
node C:/tmp/mm-r3a-pw/acceptance.mjs <新的运行名称>
node C:/tmp/mm-r3a-pw/verify.mjs
```

脚本拒绝覆盖已有 run 目录；重跑会在新副本追加中性档案及开场会话，不应对原数据运行。verify.mjs 对本次 run3 固定核对，不是通用测试入口。

证据根目录：`C:/tmp/mm-cr3a-data/pw-acceptance-20261114`。

- `run3/results.json`：16/16 及请求清单；`run3/events.jsonl`：对话框类型、决策、文档重载、步骤结果。
- `run3/script.mjs`：实际通过的测试脚本；`tool-package-lock.json`：测试依赖锁；`verification.json`：保全与落盘交叉核对。
- `run3/07-avatar-desktop.png`、`10-profile-mobile.png`、`11-single-mobile.png`、`12-create-mobile.png`、`13-drawer-mobile.png`、`13-group-mobile.png`、`15-sessions-mobile.png` 等：真实页面截图，不是生成图。已逐张查看桌面头像、群聊及成员抽屉截图；未观察到已测试页面的遮挡或横向溢出。
- `prior-hashes.json` 的 **44 个原数据/报告/日志文件全部一致**；`source-hashes.json` 的 **88 个角色模块文件和项目依赖文件全部一致**。统计仅覆盖列出的范围，不扩大为所有历史存档。

## 首次失败与既有失败保留

- run1：2/3；刷新被取消后，Playwright 的 reload 仍等待导航直到超时。修正测试脚本允许该取消结果，但仍强制收到 beforeunload.dismiss，且继续断言同一 timeOrigin 和原草稿。没有忽略真实导航失败或修改页面逻辑。
- run2：12/13；测试错误地把成员名称和“在场”拼成一个 exact 文本节点，实际 DOM 分别为 strong/small。按真实 DOM 修正名称定位；截图已证明抽屉和正确身份可见。
- run3：16/16；上述原脚本、失败截图、日志均保留，未覆盖或删改。
- 原始 `git diff --check` 仍存在已有 CRLF 行尾诊断，结果保留在本轮 `diff-check.log`；没有批量改行尾、修改指纹或关闭检查。暂存区为空。
- 前轮 58/58 离线回归、两个构建通过记录仍引用原报告；生产代码未变，本轮不重复运行。既有三项世界失败、三项帮助失败、24 项旧截图诊断及环境首次失败继续保留，没有声称修复。

## R3b 条件

R3a 的可自动化桌面/窄屏页面门禁已补齐，可据此开展 R3b 方案确认。进入 R3b 实现前仍须：

1. 用户验收当前完整流程；Windows 选择器取消、真机键盘/触控/系统返回继续单独跟踪，未实测不得改为通过。如决定延后这些设备项，需明确记录该决断。
2. 明确消息编辑、重生成、候选、分支的不可变来源、原始响应/回执保留、模板及身份快照继承、摘要/记忆失效规则。
3. 保留旧失败与行尾诊断的归因，正式提交前处理提交门禁。本轮结果不是最新主线组合兼容、CI、共享部署或真实模型验收证明。

未自动启动 R3b。真实模型测试另需模型、次数、预算、材料与停止条件授权。

## 预览与回退

- 档案：http://127.0.0.1:43158/characters?desk=profiles
- 会话：http://127.0.0.1:43158/characters?desk=sessions
- 群聊：http://127.0.0.1:43159/groups

测试浏览器已关闭；独立 loopback 预览保留。回退只停止本轮自有预览进程，保留工具目录、数据与证据，不操作其他浏览器或共享服务。

API 依据：[Playwright 对话框说明](https://github.com/microsoft/playwright/blob/main/docs/src/dialogs.md)、[Page.close](https://github.com/microsoft/playwright/blob/main/docs/src/api/class-page.md)、[FileChooser](https://github.com/microsoft/playwright/blob/main/docs/src/api/class-filechooser.md)。
