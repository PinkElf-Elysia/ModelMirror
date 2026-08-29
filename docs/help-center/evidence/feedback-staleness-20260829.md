# 帮助中心文章反馈：真闭环实现记录

- 记录日期：`2026-08-29`
- 仓库：`E:\ModelMirror\ModelMirror-new`（当前 `feature/help-center-round2`）
- 范围：帮助中心文章"这篇对你有帮助吗？"反馈功能

## 现状：真闭环（已上报后端数据库）

**反馈功能已建立真实上报通道：用户评价写入后端 JSON 文件，维护者可查询统计，前端展示实时评价标签。** 同时具备防重复（anonymous_id + slug 唯一）与防刷（IP 限流）。

## 功能实现

### 1. 后端（`server/help_feedback_store.py` + `server/main.py`）

- **存储**：文件型 JSON Store，`server/storage/help_feedback/feedback.json`（可用 `HELP_FEEDBACK_STORAGE_DIR` 覆盖）。
- **POST `/api/help/feedback`**：接收 `{ slug, article_version, value, anonymous_id }`。
  - `201`：首次评价成功。
  - `409`：同一 anonymous_id + slug 已评价过（防重复）。
  - `429`：同一 IP 60 秒内超过 5 条（防刷）。
  - `422`：参数非法（value/slug/anonymous_id 缺失或非法）。
- **GET `/api/help/feedback/stats?slug=xxx`**：返回 `{ slug, total, helpful }`，供前端标签与维护者统计。
- **防重复**：`HelpFeedbackStore.add_feedback` 检查 `(anonymous_id, slug)` 唯一，重复抛 `DuplicateFeedbackError`。
- **防刷**：`help_feedback_rate_limit_or_raise` 滑动窗口，同 IP 60 秒 >5 条返回 429。

### 2. 前端（`client/src/components/help/ArticleFeedback.tsx`）

- 点选后写 localStorage（本机记住，避免重复询问）+ POST 上报。
- 上报成功显示"你的意见已发送给团队"；409 静默；网络失败降级"本次提交未送达（仅保存在本机浏览器）"。
- **统计标签**：挂载时 GET stats，显示"已收到 N 人评价，M 人认为有帮助"。
- `client/src/utils/anonymousId.ts`：生成/复用浏览器匿名标识（随机 UUID，存 localStorage）。

### 3. 数据模型

每条记录：`{ id, slug, article_version, value, anonymous_id, created_at }`。

**不记录**：IP、真实身份、任何正文内容。`anonymous_id` 是随机 UUID，无法反查到用户。

## 防护层级

| 层级 | 机制 | 状态码 | 防什么 |
| --- | --- | --- | --- |
| 前端 localStorage | 记住选择，隐藏按钮 | — | 正常用户误触重复 |
| 限流 | 同 IP 60 秒 >5 条 | 429 | 脚本批量刷评价 |
| 防重复 | (anonymous_id + slug) 唯一 | 409 | 同浏览器同文章重复评价 |
| 参数校验 | value/slug/anonymous_id 校验 | 422 | 非法输入 |

## 已知局限（匿名方案固有）

- 清空浏览器数据 / 换浏览器 → 新 anonymous_id，可再次评价。
- 多人共用一台电脑 → 共享一个 anonymous_id。
- 匿名标识是前端生成的，后端无法验证真伪；限流（429）是主要防刷手段。

## 验证

- 后端：`server/tests/test_help_feedback.py` 4 项（写入+统计、防重复、同用户多文章、空统计）。
- 前端：`ArticleFeedback.test.tsx` 8 项（首次询问、上报成功、统计标签、409、离线降级、按文章隔离、跨文章重置）。
- 帮助中心专项测试通过；类型检查通过。
- 全量前端测试待跑。

## 未验证 / 未覆盖

- 真实浏览器端到端（前端 → 后端 → 文件写入 → 统计刷新）交互观感。
- 多进程/多实例并发写入（当前为单实例文件 Store，依赖 RLock 串行化）。
- 大量数据下的存储性能（JSON 全量读写；反馈量极大时需考虑 SQLite 或分片）。
