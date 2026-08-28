# R8A 多模态 Provider 控制面帮助增量重放记录

## 基线与隔离边界

- 验证日期：`2026-08-28`。
- 原始实施基线：`origin/main@f0150fb5daebcf5a4a70b0f350e6129dae7ff8d0`。
- 收尾验证基线：`origin/main@172137fc752803be68e9a319a8ba23ea83d4142f`。收尾期间主线新增
  Workflow RSS；交叉审计确认仅与 R8A 的三个默认关闭配置文件相交，安全快进并重跑全量门禁。
- 工作树：`C:\tmp\modelmirror-provider-multimodal-r8a`。
- 独立预览：前端 `http://127.0.0.1:15151`，后端
  `http://127.0.0.1:18151`；使用独立且保留的 Router 数据目录，不连接或修改共享 Provider 数据。
- R8A 不接管真实多模态数据面，不执行模型认证或付费调用。

## 用户路径重放

1. 打开 `/settings?section=providers`，确认三栏 Provider Control Plane 与“其他集成”仍存在。
2. 未配对状态只显示管理员配对入口；Marble 保持在 Provider 门禁之外且仍可见。
3. 打开 `/help/recover-unavailable-feature`，确认新增问题明确说明：R8A 只建立 scope、
   Adapter、Binding 和状态基础，R8B—R8F 接入前认证与 Managed 激活保持阻断且不产生费用。
4. 帮助页面控制台 warning/error 为 0。
5. 最新镜像重建后，后端健康与前端设置入口均返回 200；Router SQLite 为 v18，R8 认证会话为
   0 条，公开 `chat_image` 状态为 `feature_enabled=false`、`status=legacy` 且在派发前阻断。
6. 配对后 Provider 页面可读取连接、Inventory 与资格状态；选择“图片 Chat 流”时只显示
   OpenRouter 与 OpenAI-compatible 两种匹配 Adapter，认证按钮明确说明 R8A 不付费并保持禁用。
7. 路由页的“Chat 图片理解”显示部署开关关闭、数据面“当前子轮次未接入”、Managed 激活禁用；
   视频生成只显示 OpenRouter Video Jobs，Realtime 只显示 OpenAI Realtime SDP。
8. 浏览器 localStorage 与 sessionStorage 均为空，HttpOnly 管理 Cookie 对页面脚本不可见；配对后
   Router 仍为 0 个连接、0 条多模态认证会话，浏览器控制台 warning/error 为 0。

## 配对阻塞的证伪与修复

- 首次真实页面配对返回 200，但随后的连接与资格 GET 立即返回 401；因此未把“已解锁”提示当成
  成功证据。
- 脱敏复现确认前端 Node 代理把两个 `Set-Cookie` 合并后，Cookie 存储数为 0、连接接口为 401。
- 根因是代理使用 `Headers.forEach()` 收集响应头，丢失 `Set-Cookie` 的多值语义。
- 最小修复使用 Node 22 `Headers.getSetCookie()` 取得数组，并通过 `ServerResponse` 以多值头发送；
  后端认证、Cookie 属性、会话语义和 React 页面均未改变。
- 修复后同一脱敏复现为：配对 200、保存 1 个适用于 `/api/router` 的 HttpOnly Cookie、连接接口
  200；浏览器配对与两个 Settings 页面重放随后通过。

## 自动验证

- 严格证伪与 Workload Control 专项：43 项通过；新增证伪用例先复现并随后封堵并发重复派发、
  已知异步任务重启误转 uncertain、未派发成功状态和重启后 Call 状态遗漏。
- 受影响后端组合：374 项通过；Worker 构建物隔离复核：75 项通过。
- 最新基线后端全量：5078 项通过、29 项跳过、6 条既有弃用/字段定义警告。
- Settings 与帮助专项：21 项通过；前端常规门禁：123 个 Vitest 文件、766 项通过，随后
  Node 多 Cookie 代理回归 1 项通过。
- 本地 production build 与独立 Docker 前端构建均通过，二者都成功执行 `tsc -b`；只保留仓库
  既有的大 Chunk 告警。
- 单独 `npm.cmd run typecheck` 在 host 因 `node_modules/.tmp/*.tsbuildinfo` 的 ACL 返回 TS5033；
  这是增量写入文件的环境失败，不替代或推翻两次成功的构建集成类型检查。
- Core、独立 newAPI 与 Overlay Compose 均通过 `config --quiet`；后端 `py_compile`、
  `git diff --check` 与定向敏感信息扫描通过。

## 未验证边界

- 本记录不证明任何图片、PDF、音频、视频或 Realtime Provider 已取得资格。
- 未创建截图：受影响帮助文章此前没有截图基线；本次先保留可复核的 DOM、测试和独立预览
  证据；配对前后 Settings 均已完成浏览器重放。
