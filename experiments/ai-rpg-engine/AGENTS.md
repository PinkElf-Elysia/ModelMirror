# AGENTS.md — AI RPG 独立实验模块

本文件适用于 `experiments/ai-rpg-engine/**`，并在父级 `AGENTS.md` 基础上收紧 RPG-01 的边界。

## RPG-01 强制边界

1. 本轮只允许修改 `experiments/ai-rpg-engine/**` 与 `docs/ai-rpg-experiment/**`。
2. 固定基线为 `origin/main@06ef51ae8d58c4e33029f02ab7263e24066734b2`；基线漂移时停止并重新审计。
3. 禁止依赖父仓 `client/`、`server/`、根配置、Docker、CI、RAG、记忆、现有插件系统或 Matrix Oasis 实现。
4. 禁止模块外 `file:` / `link:` 依赖、外部或损坏的符号链接、绝对导入路径及目录逃逸。
5. 合同运行代码必须同步、纯函数、无 I/O、无网络、无模型调用，并且不得修改输入。
6. 卡包是纯数据；脚本、任意 HTML 字段、工具调用、网络调用及自动安装、启用或升级字段必须拒绝。
7. RPG-01 不实现运行时、模镜接入、提示词编排、资源转换、UI、市场、插件加载或目标站探针。
8. 每个语义批次最多修改五个文件；当前批门禁失败时不得进入下一批。
9. 不提交依赖目录、构建产物、日志、测试报告、密钥或真实 `.env`。
10. 用户人工验收前不进入 RPG-02，不 Commit、Push、创建 PR、部署或发布。

## 本轮公开合同

- `modelmirror.ai-rpg.card-package/0.1.0`
- `modelmirror.ai-rpg.player-setup/0.1.0`
- `modelmirror.ai-rpg.turn-exchange/0.1.0`
- `modelmirror.ai-rpg.plugin-manifest/0.1.0`

RPG-01 只冻结 Schema、稳定诊断、代表 fixture 和静态插件就绪计算。模型、凭据、预算、路由、会话与记忆仍由模镜治理。

## 验收

```powershell
npm.cmd ci
npm.cmd run test:boundary
npm.cmd run test:contracts
npm.cmd run verify:rpg01 -- --base 06ef51ae8d58c4e33029f02ab7263e24066734b2
```
