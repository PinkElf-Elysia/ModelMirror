# 帮助中心搜索升级记录

- 记录日期：`2026-08-29`
- 仓库：`E:\ModelMirror\ModelMirror-new`（当前 `feature/help-center-round2`）
- 范围：P1-1 搜索升级

## 升级内容

原 `searchHelpContent` 为朴素子串匹配，只搜 `title/summary/keywords`，无排序、无近义词、无空态引导。本次升级（均在 `client/src/content/help-center/index.ts` 内完成，UI 契约不变）：

### 1. 全文检索
`HelpSearchEntry` 新增内部字段 `body`（仅用于搜索，不参与展示）：
- 文章：正文 Markdown 全文（`article.content`）
- 模块主题：`topic.points` 拼接

### 2. 中文近义词映射（`HELP_SYNONYMS`）
约 30 组日常说法 ↔ 帮助术语映射，如：
- 看图 → 图片识别/图片理解/视觉
- 多步 → 工作流/流程/流水线
- 收费 → 费用/价格/计费/账单
- 智能体 → Agent
- 运维 → Runtime/运行/诊断

`expandQuery` 把查询词展开成"原文 + 近义词"，并支持反向（近义词 → 原词），提升召回。

### 3. 加权排序（`scoreEntry`）
- title 命中 +4、keywords +3、summary +2、body +1
- 命中来源决定分数，未命中为 0（修复了初版把 kindBonus 无条件加入导致的"全部命中"bug）
- 同分时按内容类型 tiebreak：文章 > 模块主题 > 一级索引 > 模块

### 4. 空态建议词（`getHelpSearchSuggestions`）
- 无结果时基于近义词映射给出可尝试的搜索词
- 兜底固定任务词：图片/费用/不可用/Agent/工作流
- `HelpCenterPage.tsx` 空态渲染建议词按钮，点击直接发起搜索

## 关键修复

初版 `scoreEntry` 把 `kindBonus`（article:4/topic:3/...）**无条件**加入分数，导致所有 entry 恒 score>0，`filter(score > 0)` 失效——无结果查询会返回全部条目。已修复为：分数纯由命中产生，kind 只作为排序 tiebreak（`KIND_ORDER`）。该 bug 由"搜索无结果时显示空态"测试暴露。

## 验证

- 帮助中心专项：`33/33` 通过（含新增近义词、全文、排序、建议词断言）。
- 类型检查：`npm run typecheck` 通过。
- 全量前端测试：待后台完成确认（预计 124 文件无回归）。
- 验证命令：
  ```
  npx vitest run src/content/help-center/helpContent.test.ts src/pages/HelpCenterPage.test.tsx --configLoader runner --maxWorkers=1
  npx tsc -b --pretty false
  ```

## 未验证

- 真实浏览器中的搜索体验与排序观感（近义词映射是否贴合真实用户用词）。
- 更多近义词的覆盖（当前 30 组，随内容增长需持续补充）。
