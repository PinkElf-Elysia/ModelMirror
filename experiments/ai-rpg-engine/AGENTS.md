# AGENTS.md — AI RPG 独立实验模块

本文件适用于 `experiments/ai-rpg-engine/**`，并在父级 `AGENTS.md` 基础上收紧 RPG-01 至 RPG-03 的边界。

## RPG-03 03A1 强制边界

1. 固定仓库基线为 `80221379cec850a2b25f5eeeb410233062f3e1ea`，固定分支为 `codex/ai-rpg-rpg03-runtime`；基线、分支或允许范围漂移时停止。
2. 只允许 `experiments/ai-rpg-engine/**`、`docs/ai-rpg-experiment/**`，以及 `server/main.py`、`server/tests/test_provider_chat_stable_chat.py`、`docs/MODEL_PROVIDER_CONTROL_PLANE.md` 三个精确父仓文件发生变化；03A1 不修改三个父仓文件。
3. `docs/RPG03_BASELINE.json` 的 67 个文件按基线 Git blob 和当前工作区字节双重冻结；旧合同、content、fixture、测试、门禁、资源与 Skill 不改。
4. `runtime/**` 的纯端口层禁止 I/O、网络、环境变量、子进程、动态加载和源码执行，只可额外使用锁定的 `ajv/dist/2020.js` 校验合同，且不得反向依赖 `runtime/node*`、`tooling/`、`scripts/` 或 `tests/`；`runtime/node.mjs` 与 `runtime/node/**` 才可使用声明内建完成文件、hash、配置和可信模镜 HTTP 适配，不接受任意依赖。
5. `tooling/runtime-*.mjs` 只做端口编排；loopback 网络与验证子进程只开放给边界声明中的精确文件。所有层禁用动态 `import()`、`eval`、`Function` 和 `vm`。静态守卫不是安全沙箱，并保守拒绝纯 runtime 的模板插值。
6. 禁止父仓源码导入、绝对或逃逸导入、`file:`/`link:` 依赖、外部或破损符号链接、敏感数据及生成物进入版本控制。候选服务与测试临时目录只放在模块内 `.rpg03-work/`。
7. `--bootstrap` 仅允许 03A1 暂时保留 package `0.2.0`；默认门禁要求 `0.3.0`。本批不修改 package/lock，不进入 03A2。

## RPG-02 02A1 强制边界

1. 固定仓库基线为 `a43cfa389e1785a95f04a006ba26550a5a36965e`，固定分支为 `codex/ai-rpg-rpg02-content`；基线、分支或允许目录漂移时停止。
2. 只允许 `experiments/ai-rpg-engine/**` 与 `docs/ai-rpg-experiment/**` 相对基线、暂存区、工作区及未跟踪变化。
3. RPG-01 根公开合同、28 项合同测试、四个 fixture、旧护栏脚本与历史验收收据按 `docs/RPG02_BASELINE.json` 冻结。
4. `content/**` 为纯 content 层：仅可依赖模块内冻结合同、`ajv`、`parse5`、`acorn`；禁止 I/O、网络、子进程、环境变量、`eval`、`Function`、`vm` 与动态加载。RPG-02 的 `/content` 子入口后续在 `content/index.mjs` 建立，冻结的 `src/index.mjs` 不改。
5. `scripts/**`、`tooling/**`、`tests/**` 为 tooling 层；仅允许边界声明列出的必要 Node 内建，禁止网络、模型与任意源码执行。只有 `scripts/check-boundary-rpg02.mjs` 可启动本地验证子进程；冻结 RPG-01 测试中的既有 Git fixture 调用是按哈希保留的历史例外。文件、hash、ZIP 与 CLI 后续只能进入 `tooling/**`。
6. 禁止父仓导入、绝对或逃逸导入、`file:`/`link:` 依赖、外部或破损符号链接、密钥及生成物进入版本控制。
7. `--bootstrap` 只用于 02A1 在 package/lock 尚未推进时运行边界门禁；默认完整验证不得借此放松依赖检查。
8. 02A1 不修改 package、lock、冻结源码、fixture、旧脚本或 RPG-01 文档，不进入 02A2。

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
