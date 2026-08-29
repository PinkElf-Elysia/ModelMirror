# 帮助中心反馈与内容陈旧闭环记录

- 记录日期：`2026-08-29`
- 仓库：`E:\ModelMirror\ModelMirror-new`（当前 `feature/help-center-round2`）
- 范围：P1-2 反馈与内容陈旧闭环

## 升级内容

### 1. 文章反馈（`client/src/components/help/ArticleFeedback.tsx`）

每篇文章底部新增"这篇对你有帮助吗？"，提供「有帮助 / 没帮助」两个按钮：

- 选择一次后，结果写入 `localStorage`（key `help-feedback:<slug>`），本地记住，不再重复询问。
- 已反馈的后续访问显示"感谢反馈"确认态，并附相近内容引导。
- **无后端上报**（按既定方案：先本地存，后续可升级）。
- localStorage 不可用（隐私模式等）时静默失败，不阻塞阅读。
- 按 slug 隔离，文章之间不串扰。

### 2. 内容陈旧提示（`HelpArticlePage.tsx` 的 `ArticlePage`）

每篇文章正文前新增验证状态提示条：

- **PENDING 草稿**：琥珀色提示"这篇帮助仍在完善中，操作步骤以当前界面为准"。
- **已发布文章**：灰字提示"本文基于 {verifiedDate} 的界面验证，产品更新后部分按钮名称、入口或价格可能变化"。

这为"内容可能陈旧"建立可见信号：作者与用户都能感知内容基于哪个版本验证，为后续"内容过期检测"（对比当前 main HEAD 与文章 verifiedCommit）预留了入口。

## 关键设计决策

- **反馈存 localStorage 而非后端**：符合"先本地存，后续可升级"的既定方案，避免后端 + 数据库改动。
- **陈旧提示基于 verifiedCommit/Date**：复用文章元数据里已有的验证信息，不新增数据源；PENDING 复用起草阶段的状态标记。

## 验证

- 新增 `ArticleFeedback.test.tsx`：5 项测试（首次询问、有帮助记住、没帮助记住、已答不再问、按文章隔离）。
- `HelpCenterPage.test.tsx` 新增 3 项：PENDING 提示、已验证日期提示、文章页反馈出现。
- 帮助中心专项：`33/33` 通过（含反馈 + 陈旧）。
- 类型检查：`npm run typecheck` 通过。
- 全量前端测试：待后台完成确认。

## 未验证

- 真实浏览器中反馈按钮的交互观感。
- localStorage 在其他浏览器隐私模式下的表现（已做降级处理）。
- 更完整的内容过期检测（对比当前提交与 verifiedCommit 的偏差）——本轮只做了"可见提示"，未做自动阈值检测。
