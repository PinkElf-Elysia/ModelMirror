# R2.9 持久受限重试帮助中心验收记录

## 基线与隔离范围

- 验收日期：2026-09-01。
- 分支：`codex/workflow-r29-restricted-retry`。
- 实现提交：`48254740`，基于主线 `cbb50f10`。
- 隔离前端 `15210`、后端 `18210` 和临时 Workflow Store；未修改主工作区、共享容器或共享持久化数据。
- 本轮只为 V2 安全 HTTP GET、只读数据表查询和合格的本地知识检索提供持久受限重试；没有扩展为通用重试。

## 真实预览工作流

1. `wf_a1af14fce21e47caaf55216d28b3163a`：确定性 503 后等待 5 秒，第 2 次返回 200，只走普通成功出口。
2. `wf_fab9ea30c97e47e0911ae0e0f7beb6e6`：确定性 429 连续失败，次数耗尽后只进入红色错误出口，安全回执记录实际 attempts。
3. `wf_9aef41e3bcda4878a711baa96e02a0d2`：SQLite BUSY 进入持久等待；服务重启及重复 Coordinator 后只恢复一次。
4. `wf_85cafa63c9e84f2ba27f8c1b822d30b4`：真实公网 `https://httpbin.org/get` 合成 GET 成功；没有发送用户数据、仓库代码或真实密钥。

上述工作流保留在隔离预览器中供人工复核。公网可达性只作为补充证据，资格、安全门禁和恢复语义由确定性测试证明。

## 帮助截图

- 文章：`/help/handle-workflow-node-failure`。
- 截图：`client/public/help-center/48254740/workflow-restricted-retry-config.png`。
- 来源：隔离预览器中的 429 耗尽工作流，真实展开 V2 安全 HTTP 节点的“失败处理”区域后捕获。
- 可见状态：Registry v5 已正常加载，面板确认“临时故障最多尝试 2 次，仍未成功时进入错误出口”，并展示 5/30 秒固定退避、受限资格说明、耗尽后错误分支和 `retry_error` 变量。
- 文件：真实 PNG，`800 × 714`，`168769` bytes，SHA256 `5CF8B3AC483F1E89580B95023DCAFB0B01AB7E3A03B0BFE9DEF7FF40EC8E8523`。
- 截图不含凭据、Token、用户数据、请求或响应正文，也没有通过生成式图像补画界面。

## 自动验证摘要

- 重基后的后端全量：`5638 passed, 29 skipped`，退出码 `0`；6 条警告均为既有弃用或前向引用警告。
- 重基后的 NodeContract、错误路由、重试、Scheduler、发布及安全组合回归：`306 passed`。
- 前端全量 Vitest：`131` 个文件、`904 passed`；安全响应头 Node 测试：`1 passed`。
- 帮助中心定向 Vitest：`2` 个文件、`45 passed`；帮助图片真实格式、尺寸、体积、引用、残留资产、替代文本和基线门禁全部通过。
- 前端生产构建通过；仅保留既有 chunk-size 警告。`git diff --check` 通过。

## 已验证边界

- 重试只发生在现有 `failureAction` 之前；成功与错误边互斥，耗尽后才停止或进入错误出口。
- HTTP 写方法、正文、DNS、SSRF、TLS、凭据、权限、配置和未知异常不得自动重试。
- 重试等待只保存节点身份、attempt、安全错误码、调度状态和必要目标指纹，不保存 URL、Header、响应、表记录或检索正文。
- 已原子持久化的 `node_retry` 等待可在重启和 lease 过期后恢复一次；本轮不宣称外部调用进行到一半时 exactly-once。
- Webhook、表单、RSS、邮件入口、可调用子流程目标、公共 App、Evaluation、Evolution 和 Planner 继续禁止重试配置。

## 回退

先关闭 `WORKFLOW_NODE_RETRIES_ENABLED` 并停止新执行，完成或取消全部 `node_retry` 等待，再停用使用受限重试的发布版本。旧运行器不得接管该 continuation；草稿、版本、执行记录和安全事件不删除。
