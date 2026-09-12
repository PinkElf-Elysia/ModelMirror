# 文本试验台设计与操作验收

日期：2026-09-12。范围：用户批准的第 1 张原型、独立实验页面与本地 HTTP 桥接。此报告的通过仅指视觉及离线操作验收。

**Findings**

当前没有未解决的 P0、P1、P2 问题。已修复的问题和复查证据如下：

| 级别 / 位置 | 初次发现与影响 | 修复 | 复查证据 |
| --- | --- | --- | --- |
| P2 / 桌面主区域高度 | 隐藏提示仍触发相邻高度选择器，底部按钮与两列高度偏离原型 | 选择器限定 `notice:not([hidden])`，调整视口扣减和侧栏间距 | `desktop-before-fix.jpg` → `desktop-first-turn.jpg` → `desktop-approved-implementation.jpg`；按钮完整可见 |
| P1 / 导出回执 | Blob 方式没有在应用内浏览器触发下载 | 改为同源 HTTP attachment，保留 Host、Origin 与 Fetch Metadata 校验 | 浏览器点击后 `downloadEvent: true`；HTTP 测试确认 attachment，跨站读取仍被拒绝 |
| P2 / 用户头像 | 文字占位没有复现原型的轮廓图标 | 使用 Lucide 官方 UserRound SVG，保留许可，几何路径未修改 | `desktop-final-first-turn.jpg` → `desktop-approved-implementation.jpg` |
| P2 / 第二轮回执 | 第二轮刚开始时可能沿用首轮的历史通过状态 | 每次接受新输入后，整体调用改为进行中，历史重新等待核对 | 离线回归断言和最终两轮演示通过 |

以上文件均在 `C:/tmp/modelmirror-openai-agents-api-v1/artifacts/agents-ui-qa/`。修改后重新打开原型和同状态实屏，在同一次比较输入中并列查看；未用构建成功替代视觉判断。

**比较目标与截图**

- source visual truth：用户批准的第 1 张生成原型，原件保留在本地验收资料中，不纳入提交。
- implementation URL：`http://127.0.0.1:8766/`，启动方式 `python -m experiments.openai_agents_api.web --demo`。
- implementation screenshot：`C:/tmp/modelmirror-openai-agents-api-v1/artifacts/agents-ui-qa/desktop-approved-implementation.jpg`。
- state：深色、首轮 READY 已完成，第二轮预设已填入但未发送。原型显示示例凭据；实现明确显示离线演示及不使用密钥，避免把模拟说成真实调用。
- viewport：桌面 CSS 1488 × 1058；原型 PNG 1487 × 1058；浏览器 JPEG 1488 × 1058。桌面截图为 1 倍像素密度，未重采样。源图相差 1 像素宽，不据此声称像素级一致。
- full-view comparison：最终比较输入同时包含上述源图与浏览器实屏。检查了导航、标题、左右区域、首轮输入输出、第二轮输入框、侧栏及底部操作。
- focused-region comparison：全分辨率图中的表单标签、模型名、按钮和状态文字均可直接辨读，无需另做裁切；针对这些区域逐项对照。
- 其他证据：`desktop-final-completed.jpg`（最终两轮结束）；`desktop-history.jpg`（两轮历史弹窗）；`tablet-cancelled.jpg`（取消）；`mobile-final-progress.jpg`（最终手机进度）。
- 平板设置 768 × 1024，DOM 宽度与 scrollWidth 均为 753；保存的可见内容截图为 753 × 1004。手机设置 390 × 844，DOM 两个宽度均为 375；最终可见内容截图为 375 × 812。应用内浏览器截图范围与设置高度存在差异，未拉伸图像；这两组用于响应式验收，不与桌面源图做等尺寸比较。
- 全页截图工具曾出现重复拼接，未用于设计通过判断；采用上列可见视口原始截图。

**五项视觉对照**

| 表面 | 实屏检查与结论 |
| --- | --- |
| 字体与排版 | 沿用项目的 Inter / Segoe UI / Microsoft YaHei 字体栈，无在线字体请求。实际标题 38px / 47.5px，正文 16px / 24.8px，输入内容 16px / 27.2px。标题、说明、正文和状态层级清楚；中文换行完整。生成原型未提供字体文件，字宽与抗锯齿无法逐像素复现，列为 P3 差异 |
| 间距与布局 | 桌面主区域宽 1424px，左右约 989px 与 419px、间隔 16px；保持原型的会话主区和窄回执侧栏。首轮状态两列与底部按钮均位于视口内。长历史在会话区滚动；平板和手机改为上下排列，所有操作可通过页面滚动到达，没有横向溢出 |
| 颜色与状态 | 采用实验页中的深色 token：页面 `#060916`、面板 `#0b1322`、正文 `#e5edf8`、次要文字 `#a3b5ce`、青色重点 `#24d9ff`。完成、错误、等待均有明确文字，不只依赖颜色。禁用与焦点状态可区分；未宣称完成全量 WCAG 审核 |
| 图像与图标 | 品牌采用项目原始 logo.png，未修改文件；无拉伸、失效图片或外链资源。用户图标来自 Lucide 官方资产，着色适配本地主题，许可写在 SVG 内。不新增 SDK、图标包或字体依赖。原型输出头像使用风格化 M，实现使用项目正式品牌资产，属有意差异 |
| 文案与内容 | 保留 OpenAI Agents API、单会话文本实验、当前会话、本次实验、回执等原型信息。增加必要的模式、费用、剩余期限和清理语义；真实/离线区别清晰。固定标记及 READY 均为合成测试内容。usage 缺失显示未知，不估算费用，不展示隐藏推理 |

**实际操作验收**

| 检查 | 实际结果与证据边界 |
| --- | --- |
| 初始状态、帮助 | 初始 0/2 轮、空输入禁用发送；帮助可打开，Escape 关闭并回到入口 |
| 预设与字数 | 填入预设后仍为 0/2 轮，没有自动提交；4097 个汉字时发送被禁用 |
| 两轮与流式进度 | 首轮显示 READY 后才可继续；第二轮显示 MM_AGENTS_V1_ALPHA，终态及模拟历史一致；期间有逐步文本与输入禁用状态 |
| 刷新 | 首轮完成后刷新，恢复同一实验和轮次，不创建新会话、不重发首轮 |
| 提前结束 | 首轮后点击结束并清理，保留结果，第二轮未执行，后续输入禁用 |
| 取消 | 在离线模拟处理中立即取消；保留部分输出，显示已确认取消及实验未通过，模拟清理结束。不能证明 OpenAI 实际取消 |
| 超时 | 演示首轮后等待超过 180 秒，页面显示期限已到和未通过，自动模拟清理。远端超时与物理清理只属于未验证边界 |
| 历史与下载 | 弹窗显示两轮输入、公开输出、ID、已保存文本和 usage 未返回；导出按钮触发应用内浏览器原生下载。脱敏 JSON 和附件头另有 HTTP 测试 |
| 两轮后停止 | 两轮结束后自动清理、显示 2/2 轮与记录已保留，第三次发送不可用 |
| 响应式与键盘 | 桌面、平板、手机实屏无横向溢出；焦点轮廓可见，输入/按钮标签进入可访问树，帮助和历史可用 Escape 关闭 |
| 控制台 | 最后一次读取浏览器 error/warn 记录为空数组 |

最终完整演示回执：`C:/tmp/modelmirror-openai-agents-api-v1/artifacts/openai-agents-api-ui/72ba70e01cfd406fb0d066b5d4735d90.json`。该回执 `evidence=offline_demo`、两轮完成、历史核对通过、模拟删除确认；`physical_cleanup=unverified`。源码清单含 11 个文件散列，设计报告本身不属于运行源码散列。

**离线命令与结果**

在 `C:/tmp/modelmirror-openai-agents-api-v1` 执行：

```powershell
& 'C:/tmp/modelmirror-openai-agents-api-v1/artifacts/agents-venv/Scripts/python.exe' -m pytest experiments/openai_agents_api/test_adapter.py experiments/openai_agents_api/test_web.py -q --basetemp 'C:/tmp/modelmirror-openai-agents-api-v1/artifacts/ui-test-run-20260912-06'
```

结果：`46 passed in 3.00s`。Provider 使用 MockTransport；仅桥接测试访问本地回环 HTTP。

以下命令均退出 0：

```text
python -m experiments.openai_agents_api.web --help
python -m experiments.openai_agents_api.smoke --help
node --check experiments/openai_agents_api/ui/app.js
git diff --check
```

帮助命令实际使用上述 venv 的 Python，并加 `-X utf8`。Windows 默认 pytest 临时目录曾有权限问题，改用已确认不存在的本任务 artifacts 子目录；未删除或改动共享临时目录。未运行前端主应用构建或业务模块全量回归，因为这些代码及依赖未改变。

**Open Questions / 未验证范围**

- 没有待决的实现范围问题。真实网页调用、真实取消、远端失败和实际异步物理清理未在本輪运行。
- 浏览器自动化覆盖主要操作，不包含多浏览器兼容、屏幕阅读器、200% 缩放、人工制造的丢失 POST 回执及进程崩溃恢复验收；后两者的拒绝重放和已知会话清理仅有离线测试及代码约束。
- 原 CLI 真实冒烟属于 UI 修改前源码；不能用于声明本网页的真实 Provider 验收通过。
- 正式帮助中心、主导航、生产部署与后续模块接入不在本任务内；相关 PR 前需单独处理正式帮助中心门禁。

**Implementation Checklist**

- [x] 保持独立实验目录，复用既有适配器，不修改业务模块或生产依赖。
- [x] 完成原型与同状态实屏比较，修复 P0/P1/P2 后再次截图核对。
- [x] 完成两轮、取消、清理、历史、下载与响应式离线验收。
- [x] 保留脱敏证据和未验证边界；未 Commit、Push、PR、Merge、Deploy。

**Follow-up Polish**

- P3：生成图与系统字体的字宽、头像尺寸及品牌输出图标有轻微差异，未改变主要区域占比或按钮可达性。若后续需要像素级还原，应先确定可分发字体和最终品牌图标规范。
- P3：回执完成状态采用圆点加文字，原型采用勾选图标；含义由文字完整表达。

final result: passed

## PR 准备复核（2026-09-12）

用户后续授权提交本轮独立实验 PR；上述“未 Commit、Push、PR”的记录描述原验收时点，不代表本次发布状态。后续能力路线尚未实现。

- 再次刷新远端 `main`，仍为 `d0aa44b36d8ae115d282a70a6871c9a5ab906214`，与独立工作树基线一致。
- 原 12 个文件在发布说明补充前均与 `artifacts/agents-ui-qa/acceptance.json` 的 SHA-256 相符。运行实现、测试及界面资产未修改。
- 复跑 `python -m pytest experiments/openai_agents_api/test_adapter.py experiments/openai_agents_api/test_web.py -q`：`46 passed in 3.08s`。实际使用原实验虚拟环境和独立的 `artifacts/pr-test-run-20260912-01` 临时目录。
- `web --help`、`smoke --help` 和 `node --check experiments/openai_agents_api/ui/app.js` 均通过。
- 审阅用截图 [docs/text-trial-demo.jpg](docs/text-trial-demo.jpg) 为已验收的 `desktop-approved-implementation.jpg` 原样副本；再次查看确认仅包含合成文本和离线状态。
- Help Center Impact：独立实验页面存在用户体验影响。实验内 README、页面帮助、此记录及实屏同属本 PR；主站正式帮助中心未同步，第 2.6 节门禁未完成，按当前限定范围提交为草稿并明确披露。
- 本次未执行新的真实 Provider 调用、浏览器交互或共享服务操作；网页真实验收及物理清理仍为未验证。
