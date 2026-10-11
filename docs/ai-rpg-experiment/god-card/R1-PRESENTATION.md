# R1：两卡展示结构与固定资源

日期：2026-10-11。状态：离线结构验收通过，运行时未接入，UI 验收未运行。

## 范围与现场

- 工作区：`C:\tmp\modelmirror-rpg-god-card`；分支：`codex/rpg-god-card`。
- HEAD：`e5a808a60b024635a1e13965558f15775c5af86a`。现有 God 实现及父仓集成仍为之前的未提交修改，不能用 HEAD 代表完整现场。
- 本批仅新增下列五个文件，不修改既有源码、提示词、原始回复、会话、账本或预览进程。
- 未调用 Provider，未 Commit、Push、PR、部署。本批不继承旧发布权限。

| 新增文件 | 用途 |
| --- | --- |
| `experiments/ai-rpg-engine/card-replica/lib/card-presentation.mjs` | 宿主指定 Earth/God 的独立展示转换器 |
| `experiments/ai-rpg-engine/card-replica/tests/card-presentation.test.mjs` | 14 项结构、混合文本、安全和资源测试 |
| `experiments/ai-rpg-engine/card-replica/public/assets/god-commentary-v1.webp` | 原站参考输出中已显示的作者图片 |
| `experiments/ai-rpg-engine/card-replica/public/assets/god-commentary-alt-v1.jpg` | 本地已保存真实输出引用的另一张作者图片 |
| 本文 | 本批合同、来源、证据、缺口及回退 |

## 原始发现与修复边界

现有 `lib/render.mjs` 删除 `input`、`img`、`style`，仅保留 `main-text` 类名；God 宏观/微观标签被展开，作者翻面、弹幕、信息卡的结构类名消失。聊天端以“含 HTML 则展示清理后的 HTML，否则 Markdown”分路，导致 HTML 折叠区内的 Markdown 围栏直接成为文字。通用聊天 CSS 不能替代作者的输出样式。

这与此前轮询造成折叠栏重置的问题不同；接入时必须继续保留 `SavedOutput` 的稳定 DOM 和 memo 行为。

R1 新接口 `cardPresentation(raw, profile)` 只产生展示副本，返回 HTML、版本及过滤诊断。它没有被任何现有路由或聊天组件导入，所以当前用户入口仍走原渲染。

- `profile` 仅接受宿主选择的 `earth` 或 `god`，模型正文不能切换配置。
- 按已观察的两卡输出保留明确类名。God 仅在对应 label/容器结构中保留两种原生 checkbox；删除模型提交的 id、name、事件及任意属性。
- 保留原生 details、表格和限定颜色；Earth 状态条只接受 0–100% 的宽度。模型提供的脚本、样式表、外部资源地址及任意布局样式不执行。
- God 宏观/微观正文、Earth 正文、折叠内围栏支持本批覆盖的 HTML/Markdown 混排。相邻内联标签不会把同一段拆开；现有 pre/code 不再二次解释；未闭合围栏只修复展示副本。
- 不是完整的任意 HTML/CommonMark 兼容引擎。未观察到的结构不承诺作者样式兼容；超深嵌套显式报错，不静默截断正文。后续宿主接入必须处理该错误并提供原文入口。
- 每个远端图片地址必须经过固定映射；未登记图片仅显示占位文本。映射不发起远端请求，不下载模型给出的任意 URL。

## 作者资源来源

本批只获取两张具体资源，无批量抓取。它们均在冻结的 God 提示词中出现；这次仅机械核对地址存在性，未改写或重新审计作者文本。

1. 原站参考输出：`https://afengy.cash/zh/explore/installed/693e42eb-9016-4248-a6af-4a321a0fa5f6?page=1`。
   - 图片：`https://catai.wiki/31a557a3-828e-44e7-bcc7-972771e4bb00/w=3000`。
   - 通过正式浏览器的已观察页面资源 bundle 获取。67,546 bytes，真实格式 WebP。
2. 本地真实回复 slot-2 使用不同图片：`https://catai.wiki/5a7a3217-dc6e-4b63-36d8-1c4b11a28d00/w=3000`。
   - 正式浏览器打开具体地址，页面确认图片 822×1200，再通过图片下载能力获取。125,057 bytes，真实格式 JPEG。
   - 首次打开调用超时，但图片页实际已创建并加载；重新查看页面后下载成功，未重复创建或绕过浏览器限制。

两张图分别保存，不用参考图替换模型实际引用的作者图。本地复刻有用户授权；这份资源登记不声明获得了额外的公开再分发许可证。

Earth 观察入口：`https://afengy.cash/zh/explore/installed/dc84b5e0-4ee4-45f2-9751-36025e3864c1`。本批保留已见人物、网格、状态条、记忆区结构；作者 CSS 及完整真实输出的可见回放留在 R2。

## 实际验证

在 `experiments/ai-rpg-engine/card-replica` 运行：

```powershell
node --test tests/card-presentation.test.mjs tests/assembly.test.mjs
npm.cmd run typecheck
```

- 最终：25/25 通过，其中新增 14 项、原装配/资源/安全测试 11 项；TypeScript 通过。
- 首次新增测试运行：11/12 通过，折叠区 Markdown 分隔线失败。修复连续文本与内联 HTML 的处理后重跑通过，随后增加混合段落和既有 pre/code 回归；保留此次失败事实。
- 安全测试包含脚本、事件、SVG/MathML、外链、远端图片、CSS URL、表单、畸形嵌套输入；这些是具体回归证据，不是任意恶意 HTML 的形式化安全证明。
- 仅读取既有 God 真实回复进行内存转换；没有重新生成或改写文件。

回放来源：`experiments/ai-rpg-engine/.rpg04-work/god-card/b4-real/data/god/real-dispatches/god/slot-2/response.txt`，9533 个 Unicode 字符。

来源 SHA256：`9c55360dfe10f72e7562e125084382a9aa2e3d87a6eccb2244ff45c00e96ddc0`，转换前后文件逐字节一致。

| 结构 | 现有 safeHtml | R1 展示转换 |
| --- | ---: | ---: |
| 带类名的元素 | 0 | 175 |
| 原生 checkbox | 0 | 2 |
| 已登记本地图片 | 0 | 1 |
| details | 8 | 8 |
| 弹幕轨道 | 0 | 3 |
| pre 代码区 | 0 | 2 |
| p 段落 | 48 | 67 |

段落数量变化来自宏观/微观文本和 Markdown 段落的显示转换，不是增加剧情内容。该统计不代表样式、动画或内容质量已通过。

整个已有工作区的 `git diff --check` 返回失败，集中报告原先修改文件的 CRLF 行尾为 trailing whitespace；本批没有修改这些文件，也没有为了报告通过而重排它们。新增文本文件另行检查行尾空白。Git 全量差异检查不能据此报告通过。

未运行：新转换器的浏览器执行与多回合交互、桌面/390px 截图、实际动画/键盘验证、生产构建、宿主全量回归。R1 未接入口，为保护正在使用的构建产物，没有覆盖现有 dist。

## 本批交付 hash

以下为文件字节 SHA256，本文自身不纳入；旧 DELIVERY 不改写成包含已上线的 R1。

| 文件（相对 card-replica） | SHA256 |
| --- | --- |
| `lib/card-presentation.mjs` | `7cd94fe89220ee19ac5d5147ae76fb902755ca2b13c559edf5b0b9a74a1b1ea2` |
| `tests/card-presentation.test.mjs` | `8fb628bd811e7d2bc7b409ddb2acc07876e6e1c281eb94cc3bdb387b4d67be73` |
| `public/assets/god-commentary-v1.webp` | `d76ff72d86bcb4dd73e104fffd838630a7e20ab87876931d5dc66cdee78c7b8f` |
| `public/assets/god-commentary-alt-v1.jpg` | `bae8e3a381d94322fdb810f2e653aeaae21a680a3c99bf97bb7990482e9163e5` |

## 下一批验收与回退

R2 在确认 R1 后进行：加入按卡片隔离的作者展示 CSS；接入宿主展示副本和固定图片 MIME/静态路由；用已保存原文回放翻面、弹幕、图片展开、折叠及 Earth 面板，核对刷新/轮询保持状态、跨回合控件隔离、键盘和减少动效偏好。浏览器确认没有非预期远端资源访问，再提供真实截图与可见入口。

本批回退无需更改任何服务：当前入口本来未使用转换器；后续只撤回以上新增文件即可。不要重置整棵工作区，不删除原件、Downloads 原始下载、会话、派发账本或历史证据。下一批回退优先让入口恢复旧转换器，保留本批资源和证据供核对。
