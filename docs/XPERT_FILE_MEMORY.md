# Xpert 类型化文件记忆

## 目标

`XPERT-MIDDLEWARE-FILE-MEMORY-08` 将 Xpert 级长期记忆从扁平 JSON 记录升级为可审阅、可冲突保护的 Markdown 文件记忆。会话级 Memory 保持原存储和隔离语义，文件记忆只属于目标 Xpert，不会因 Goal 或 Handoff 自动共享来源会话的私有记忆。

核心闭环为：

`运行信息 -> 类型化候选 -> 人工编辑/审批 -> 原子写入 -> 三层召回 -> 使用信号`

模型自动整理永远只创建候选。用户通过“记住这条”显式写入时可直接创建正式记忆。

## 存储模型

`XpertFileMemoryStore` 默认位于 `XPERT_CONTEXT_STORAGE_DIR` 下，每个 Xpert 使用不可逆的安全作用域目录，包含：

- `MEMORY.md`：由正式记忆派生的受限摘要索引。
- `user/`：用户偏好、身份背景和稳定约束。
- `feedback/`：纠正、质量反馈和回答偏好。
- `project/`：项目决定、状态、约定和持续任务信息。
- `reference/`：可长期引用的事实、说明和外部参考。
- `manifest.json`：revision、状态、来源、canonical reference、使用统计和信号。

正文文件名只使用稳定 ID 的哈希，不使用标题或用户输入。写入采用进程锁、同目录临时文件和原子替换。API 返回 `memory://xpert/<memory_id>`，不返回真实路径或正文文件名。

正式记忆包含稳定 ID、类型、标题、摘要、正文、标签、状态、revision、来源引用、置信度和使用统计。编辑、归档、候选修改与审批均使用 revision；陈旧请求返回冲突，不覆盖较新的人工修改。

## 兼容迁移

旧 Xpert 级 Memory 在首次访问该 Xpert 长期记忆时懒迁移：

- 保留原 `memory_id`、内容、标签、来源和时间。
- 无法分类的记录归入 `project` 并增加 `legacy-import` 标签。
- 会话级 Memory 不迁移。
- 迁移完成后从旧快照移除对应 Xpert 级记录，重复访问保持幂等。

旧 Memory API 继续可用，并增加可选的 `type`、`title`、`summary`、`revision`、`canonical_ref` 和 `usage` 字段。

## Agent 中间件

`xpert_file_memory` 通过 middleware binding 绑定到 `workflow_agent`。显式中间件优先；未显式绑定时，旧的 `memoryReadEnabled`、`memoryReadScope`、`memoryWriteEnabled` 和 `memoryWriteTarget` 会编译成隐式文件记忆配置。

模型调用前按三层注入：

1. `MEMORY.md` 的受限索引。
2. 确定性检索所得的相关摘要 digest。
3. 选择器选中的少量正文。

选择器可使用注册模型输出严格 JSON；超时、非法输出或模型未配置时回退到标题、摘要、标签、时间和使用信号的确定性排序。默认单轮正文预算 20,000 字符、单会话累计预算 60,000 字符。预算在会话 Store 中原子认领，避免并发调用重复消耗。

普通 `/workflow` 没有 Xpert 上下文时安全跳过持久记忆并记录 warning。已发布 Xpert、Goal 和 Handoff 读取目标 Xpert 自身记忆。公开 App 默认关闭；仅 `allow_xpert_memory=true` 时允许只读，写回始终禁止。

## 候选与使用信号

类型化候选支持 `create` 和 `update`。更新候选必须固定 `target_memory_id + base_revision`；审批时目标 revision 已变化则候选进入 `conflict`。

自动写回只分析最近最多 18 条、合计 6,000 字符的对话，并最多生成 3 条候选。相同 Xpert、内容 hash 和来源 run 的 pending 候选去重。拒绝、冲突、模型失败或索引异常不影响主回答。

安全信号包括：

- `recall_hit`
- `detail_read`
- `explicit_write`
- `candidate_created`
- `correction`
- `index_issue`

信号用于确定性排序和 UI 使用摘要。checkpoint 与 audit 只记录 ID、类型、数量、长度、策略和错误摘要，不保存正文、prompt、文件路径或密钥。

## API

- `GET /api/xperts/{xpert_id}/file-memory/index`
- `GET /api/xperts/{xpert_id}/file-memory/{memory_id}`
- `PATCH /api/xperts/{xpert_id}/file-memory/{memory_id}`
- `DELETE /api/xperts/{xpert_id}/file-memory/{memory_id}`
- `GET /api/xperts/{xpert_id}/file-memory/signals`
- `POST /api/xperts/{xpert_id}/file-memory/writeback`
- `PATCH /api/xperts/{xpert_id}/memory-candidates/{candidate_id}`

Xpert Chat 的“文件与记忆”区域提供索引状态、类型筛选、搜索、详情编辑、归档、候选编辑/审批和冲突提示。刷新页面或容器重启后，文件记忆、候选、索引和使用信号继续存在。

## 验证护栏

变更该链路至少执行：

```bash
python -m pytest server/tests/test_xpert_file_memory.py -q
python -m pytest server/tests/test_xpert_context.py -q
python -m pytest server/tests/test_workflow_native_validate.py -q
python -m pytest server/tests/test_xpert_app_api.py -q
cd client && npm.cmd run build
```

还需验证：旧迁移幂等、会话隔离、revision 冲突、选择器降级、预算限制、Goal/Handoff 目标记忆隔离、App 只读门禁和敏感信息扫描。
