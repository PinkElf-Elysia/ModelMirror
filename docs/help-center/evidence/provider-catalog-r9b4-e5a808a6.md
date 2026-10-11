# R9B4 目录准入截图与帮助证据

## 来源与验证边界

- 基线：`e5a808a60b024635a1e13965558f15775c5af86a`；日期：2026-10-09。
- 独立合成预览：`15164`，不修改其他预览、连接或数据。
- 截图时源码摘要：`fd14b6bdab588c4bf080e36bc84acccb2e4c5984f3029c031acf33138212736c`。
- 镜像：`sha256:116bce80b747d62969109e1319d7274dcabbfeb8984b7090c79503441753b8fb`。
- 预览仅包含合成连接和资格；未调用真实 Provider。截图证明状态展示，不证明真实模型可调用。
- 本次新文章与 PNG 晚于上述镜像；下文单独记录同步文件哈希和浏览器重放，不将原始镜像本身算作新文章验收证据。

## 可见状态

浏览器已在“Provider 与 Catalog”的 Provider Inventory 区域核对三个 online 连接、四个合成模型：qualified 显示“可调用”；certified-unbound 显示 contract_verified；expired 与 inventory-only 显示 manual_required。总览显示四个运行时模型中一个可调用、三个阻断。未点击刷新目录、连接测试或认证。

只读 API 与 Catalog 对比前后 SQLite 逻辑内容摘要一致；Chat Run/Attempt、Workload Call 和 Batch Job 均为零。这只证明所测只读查询没有写入或派发，不扩展为所有管理接口均无副作用。

## 原图与确定性处理

用户更正原先“暂不处理”的选择，明确要求处理。仅裁切并编码 PNG，没有重绘、改字、改状态或缩放。裁切去除连接表单、地址和无关页面，保留整个目录状态面板。

- 原图保留于本机 `C:/tmp/modelmirror-r9b4-preview-01/catalog-readiness.jpg`，不作为公开帮助资产。
- 原图 SHA-256：`0b3973a6bc7e62c23b0d92edf2155ba7a28dbdcbc8e2d99e736bab86393de6e3`。
- 裁切坐标：`(313, 94, 1228, 603)`，顺序为左、上、右、下。
- 输出：`client/public/help-center/e5a808a6/provider-catalog-admission.png`，915×509，209886 字节。
- 输出 SHA-256：`b901d4e3751b893ba86dde2dc43a12a20bc4b9d4ff567e264085d6eaa0566d5f`。
- 原图处理后哈希不变。输出已人工视觉核对，文字与状态保持原样。

新截图由独立文章 `check-managed-catalog` 引用，原 B3 资格截图及其来源基线不重新标注。文章验证基线记录本次合成 UI 来源；完整操作重放见下一节。

## 帮助同步与重放

仅向 `modelmirror-r9b4-preview-01-client` 同步新文章、帮助目录、帮助测试和 PNG；未重建或重启 Server，未改凭据、资格或策略。四个文件宿主与容器 SHA-256 一致：

| 文件 | SHA-256 |
| --- | --- |
| `articles/check-managed-catalog.md` | `da7018bac9fb6eb88d0b765ad6ac8af469737e0b0f9b36e9956e8d2262914221` |
| `index.ts` | `92d08f82fb0c4d167753b20234c84045001146acb08fb78d14cfabd53f044b29` |
| `helpContent.test.ts` | `7a58ddc17dd5d575478fff69b14177966e64f5e4086cd4d0ec6d2bb65fd746f6` |
| `provider-catalog-admission.png` | `b901d4e3751b893ba86dde2dc43a12a20bc4b9d4ff567e264085d6eaa0566d5f` |

使用新浏览器标签页、空帮助搜索与页面初始状态重放：

1. 打开 `/help/check-managed-catalog`，标题、解决问题目录入口、日期、合成数据说明和五步操作可见。
2. 滚动至示例，PNG 加载完成，实际自然尺寸 915×509；完整面板与说明无裁断。
3. 点击正文“Provider 与 Catalog”链接，进入预期分区；会话已解锁，没有输入或修改表单。
4. 核对三个 online 连接及四个模型的目录状态，与截图相同；资格卡片分别可见过期、有效和尚未认证。
5. 进入“路由与实验”，默认 Chat 为 `newapi_preferred`；当前资格快照中 expired 已失效，qualified 有效。未保存、激活或认证，未发送模型请求。

本次帮助专项 `helpContent.test.ts`：17 passed；宿主和预览内 `verify-help-images.mjs` 均通过；`git diff --check` 通过。以上为帮助增量验证，不替代最终源码冻结后的全量验收。

2026-10-11 最终验收：`acceptance-02` 已纳入新帮助文章、PNG 与 R8C 测试修正，Linux 后端 8921 passed、30 skipped、0 failed；前端 1208 passed、3 failed，均为已批准的同基线失败，typecheck、生产构建和帮助资产检查通过。完整归因见 [任务卡](../../task-cards/provider-control-r9-b4.md)，最终证据身份见 [验收归档](../../audits/provider-control-r9-b4-closeout.md)。

归档后经用户授权，工作树已接入 `a9ac1f13`，新基线结果与独立重放记录在下一节。以上原截图及历史结果仍只属于 `e5a808a6`，不改标来源；归档文字更新没有改变原截图与用户帮助文章。

## 新基线独立重放

2026-10-11，经用户明确批准，在 `15165` 启动新基线合成预览，不替换 `15164`，不复用旧数据卷。使用 `acceptance-03` 镜像 `sha256:6e201d40668ffcb551e6246c58e2c8c9ae481da60fe890ee1df981417ec22d7b`；新数据卷为 `modelmirror-r9b4-preview-02_synthetic_router`，Server 仅有 internal 网络。配对信息及 Provider 数据均为固定合成值。

浏览器重放结果：

1. `/help/check-managed-catalog` 正常展示标题、合成示例、限制及 Provider/路由链接；没有改标原图基线。
2. 通过预览专用配对后，三个连接显示 online；`synthetic/qualified` 为“可调用”，`synthetic/certified-unbound` 为 `contract_verified`，`synthetic/expired` 与 `synthetic/inventory-only` 为 `manual_required`。
3. 资格区域明确展示过期、有效和尚未认证；认证按钮因部署关闭而禁用。没有点击测试、刷新目录或认证。
4. 路由页保持 `newapi_preferred`，expired 显示 `provider_chat_certification_expired`，qualified 有效；required 仍受资格失效和纪元不可用阻断，0/500 次、0/14 天。
5. 浏览前后只读 SQLite 摘要核对：Chat/Canary/Workload 运行、尝试、Call 和 Batch 记录均为 0。进入管理路由页触发现有 `get_policy()` 的 Gate 失效同步，只有 `provider_chat_gate_epochs` 摘要变化；因此不将整页浏览描述为数据库完全不变。此项不同于公共 Readiness/Catalog 只读查询的无副作用测试。

新目录截图已在工具中视觉核对；当前浏览器工具写文件权限拒绝保存，因此没有将它登记为已归档图片，也没有替换原帮助 PNG。2026-10-11 用户明确批准复用原截图证据：保留 `e5a808a6` 路径、来源及哈希，以本节记录补充新基线实操；不能把视觉重放或复用批准冒充新图片已归档。

新基线自动验收已结束：Linux 后端 8925 passed、30 skipped、0 failed，独立 Workflow 7 passed；前端 1214 passed、1 项同基线复现且获用户批准的专家团固定模型失败。构建、类型检查和帮助资产检查通过。完整证据核验见验收归档；上述结果不证明真实 Provider 或生产验收完成。
