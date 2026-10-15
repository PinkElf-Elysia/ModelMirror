# C1 缺少可信来源帮助页：预览证据

## 来源与边界

- 日期：2026-10-15。
- 页面：独立合成预览 15168 的 `/help/recover-unavailable-feature`。
- 基线：`ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795` 加本轮未提交 C1 修改。
- `source-recovery-help.png` 为用户提供的原始 PNG，原样归档，未裁切、缩放或重绘。
- SHA-256：`65e74d433451a7af12aefc93f7056dbdbf1895fd221b8b2344aa9c911b7affc0`。
- 用户此前明确确认另一张截图来自旧 15167；旧图仅保留在本机验收目录，不作为本基线证据。

截图证明帮助页的处置说明可读：保留旧运行、核对持久来源，不以重新配对、认证或关闭控制面绕过阻断；新任务是独立调用，未派发的恢复不能证明旧调用从未发生。截图不证明真实 Provider 调用、恢复代码执行、资格或生产可用性。

## 制品关联

原独立预览镜像为 `sha256:7c4edf0ec357f6ef4a10749e0faa9c5c17cc3fe5acaf4ec4e9c99079edac6b23`。其后仅将获批的 ScaleDown PNG 编码修复和文章引用装入客户端增量镜像 `sha256:7a663a3073ec1178f54fa50a45664bf4e0bab95fbdbff98da02c3ec2b92c15a7`；C1 帮助文章内容未变，Server、合成卷及其他预览未重建。

浏览器核对增量镜像中 ScaleDown 图片加载完成，路径以 `.png` 结尾，原生尺寸 845×1634。该核对只验证图片修复及引用，不重写原 ScaleDown 截图的 2df7efba 来源。

运行恢复、测试和未完成门禁分别见 `docs/task-cards/provider-control-r9-c1.md`；本图不能替代这些证据。
