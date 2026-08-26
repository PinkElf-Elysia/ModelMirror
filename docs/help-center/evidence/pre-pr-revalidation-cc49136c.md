# PR 前最新主线重放记录

## 基线与隔离环境

- 验证日期：`2026-08-26`。
- 最新主线：`origin/main@cc49136c955aa98cab7e3877848bacd9e3381126`。
- PR 工作树：`C:\tmp\modelmirror-help-center-round1-pr-20260826`。
- Compose 项目：`modelmirror-help-r1-pr-cc49136c`。
- 前端：`http://127.0.0.1:15306`；后端：`http://127.0.0.1:18306`。
- 容器未挂载共享目录或卷，未加载仓库 `.env`；OMNIROUTE、Agent Workspace、远程 MCP 与 Coding 能力均关闭。

本记录用于关闭旧基线 `d4bd6b8d` 与最新主线之间的差异。旧记录仍保留第一次事实发现、完整二级索引排查和候选设计对照，不作为最新状态证明。

## 第一次使用路径重放

使用应用内预览器，在 `1440 × 900` 视口重新执行教程路径：

1. `/models` 显示“AI 牛马招聘会”、六项资源导航和独立“帮助”入口。
2. 选择“图片”和“图片识别”后，两项均保持选中，结果列表更新。
3. `MoonshotAI: Kimi K3` 同时显示“图片识别”和“图片”，入口为“立即面试”；可见价格仍为输入 `¥20.31`、输出 `¥101.55`。
4. 进入 `/chat/moonshotai%2Fkimi-k3` 后，页面显示“开始一段新对话”和“添加内容与工具”。
5. 点击加号后，菜单显示“文件”“视觉 / OCR”“图片”“音频”“视频”等选项；“图片”可用，“视觉 / OCR”和“视频”显示当前环境不可用的原因。
6. 点击“图片”触发系统文件选择器，`multiple=true`。没有选择文件、上传内容、输入消息或发送模型请求。

最新用户截图来自同一次预览，均为宽 1000px 的真实 PNG，单张小于 250KB：

- `client/public/help-center/cc49136c/model-market-image-understanding.png`
- `client/public/help-center/cc49136c/kimi-k3-add-image-menu.png`

## 主线交叉页面复核

- `/mcps?view=hub` 仍包含“工具货架、已连接注册表、MCP Hub”三项页签。页面当前明确说明：Registry 收录不代表安全认证；功能、远程试连和 OAuth Runtime 默认关闭；OAuth 工具只有在 V3 契约、当前 Token revision 与 Schema 匹配时进入 Runtime，且每次调用都需审批。帮助文案已按可见页面更新，MCP 的四项二级结构不变。
- `/rag` 仍以“知识库管理”为主标题，空白状态从“新建知识库”开始；最新页面补充了知识流水线与文件类型说明，但 RAG 仍属于“工作台与设置”二级索引，本轮不把流水线内部步骤提升为二级索引。
- `client/src/App.tsx` 对外路由集合在旧基线与本基线之间没有影响本轮一级、二级索引归类的变化。

## 帮助中心候选验收

- 从 `/help` 的可见链接开始自动发现并遍历全部 `64` 个帮助路由：首页、五篇正式文章、五个一级索引、八个一级模块和 45 个二级索引。每页只有一个 `h1`，没有合法路由进入帮助中心找不到页面。
- `/help/modules/agents/expert-team` 的一级标题为“专家团”，并显示在 Agent 分级目录下；一级模块仍为“模型、Agent、MCP、Skill、提示词、运维、工作台与设置、实验功能”。
- 未知路径 `/help/not-a-real-article` 显示帮助中心内的“这篇帮助不存在”，保留分级目录和恢复入口。
- 搜索、一级索引、模块和二级功能链接均使用统一帮助阅读框架；模块页显示完整二级目录，首页每个模块只展示两个入口。
- 桌面 `1440 × 900`、移动 `390 × 844` 均无横向溢出；移动文章目录和帮助目录默认折叠。
- 最低 `320 × 844` 复测时 `documentElement.scrollWidth=310`、`clientWidth=310`，无横向滚动。为适配浏览器占用 10px 的垂直滚动条，将全局最小宽度改为 `min(320px, 100%)`，并让入门栏在窄屏收缩和隐藏非必要进度条。
- 教程页两张图片均加载完成、替代文本非空，`naturalWidth=1000`。
- 全部浏览器重放结束后控制台错误为 `0`。

## 自动验证

```powershell
cd client
npm.cmd run typecheck
npm.cmd run test:run -- src/content/help-center/helpContent.test.ts src/pages/HelpCenterPage.test.tsx --maxWorkers=1
npm.cmd run test:run -- --maxWorkers=1
npm.cmd run build
```

- 帮助中心专项：`23/23` 通过。
- 全量前端：`118` 个测试文件、`701/701` 项测试通过。
- 类型检查：受限进程第一次因不能创建 `node_modules/.tmp` 增量缓存返回 `TS5033/EPERM`；允许写入仓库既定缓存目录后，同一条 `npm.cmd run typecheck` 通过，没有 TypeScript 诊断。
- 主机生产构建通过；隔离 Docker 客户端也重新执行标准 `npm run build` 并通过。两者仅保留仓库既有的大 Chunk 警告。
- Compose 配置有效，隔离后端健康，帮助页面与两张最新截图均可通过 HTTP 访问。

## 未验证边界

- 未选择或上传图片，未验证上传后的预览、真实模型回复、回复质量、真实计费和数据处理合同。
- 未执行需要凭据、权限、外部连接或实验开关的功能。
- 模型目录、状态与价格会继续变化；教程要求用户在操作前重新查看当前页面。
