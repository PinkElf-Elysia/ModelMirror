# 安全政策

## 支持范围

安全修复以默认分支 `main` 为支持范围。历史提交、未合并分支、未明确维护的标签或本地工作树不构成独立的安全支持版本。

本政策不承诺固定的响应或修复时限。报告状态与后续协调以对应的 GitHub Security Advisory 私密会话为准。

## 私密报告漏洞

请使用仓库的 [GitHub private vulnerability reporting](https://github.com/PinkElf-Elysia/ModelMirror/security/advisories/new) 提交安全问题。

报告中建议包含：

- 受影响的路径、提交或功能入口。
- 可重复但不包含真实凭据的复现步骤。
- 潜在影响、已观察到的边界以及建议的缓解方式。
- 已脱敏的日志、截图或最小样例。

禁止通过公开 Issue、Pull Request、Discussion、提交信息或 CI 日志披露漏洞细节，也不要粘贴任何真实 secret、API key、token、密码、Cookie、私钥或用户数据。

如果问题涉及仍然有效的凭据，请先在对应提供方撤销或轮换凭据，再通过私密报告提供凭据类型、受影响路径和时间范围等脱敏信息。删除文件或改写 Git 历史不能替代凭据轮换。
