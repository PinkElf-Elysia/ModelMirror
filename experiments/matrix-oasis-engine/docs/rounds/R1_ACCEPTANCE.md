# R1 验收记录

状态：工程验证已完成；等待用户人工验收

固定基线：`8deeebb85d2db1b7f1b3564fca984503ce5787a2`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [x] 全部 R1 变更位于 `experiments/matrix-oasis-engine/**`，且 `apps/creator-web/**` 零差异。
- [x] 通用 Authoring Game Pack 可由单个 JSON 表达并由确定性验证器校验。
- [x] 非法引用、重复 ID 与非法交互图返回稳定、可定位诊断。
- [x] `npm run verify`、固定基线范围检查与历史保留型拆分全部通过。
- [x] 父源码、配置、Matrix Oasis 页面和共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R1.1 | 治理与隔离契约升级 | `a3a5c13` | 已完成 |
| R1.2 | 通用 Game Pack 合同 | `50683de` | 已完成 |
| R1.3 | 确定性验证器 | `c2b2fc1` | 已完成 |
| R1.4 | 单 JSON 验收样例与负向测试 | `b9cfca2` | 已完成 |
| R1.5 | 拆分证据与人工验收包 | 本批次提交；最终 SHA 见仓外交付清单 | 已完成 |

## 文件树与产物摘要

- 模块根：私有 `UNLICENSED` npm workspace、固定轮次边界、独立 lockfile、LF/二进制属性和回退文档。
- `apps/creator-web/**`：冻结的 R0 空壳，R1 字节级零差异。
- `packages/game-pack-contracts`：Authoring Game Pack 0.1.0 权威 JSON Schema 与只读导出。
- `packages/game-pack-validator`：无 I/O 的严格 JSON、Schema、引用、类型和静态图验证器。
- `examples`：题材中性的机制权威夹具，以及可替换的“末班地铁：回声十三站”薄型集成夹具。
- `scripts` / `tests`：doctor、范围/边界、CLI、样例、拆分与确定性负向门禁。

两个样例的规范 LF 字节 SHA-256：

| 样例 | SHA-256 | 角色 |
| --- | --- | --- |
| `mechanics-conformance.authoring-game-pack.json` | `55896eaa631f2b563df163f77002924e4e6ea1d3a9d421dc383e777c172aa119` | 通用机制回归权威 |
| `last-train-r1.authoring-game-pack.json` | `c98b277d8e960404658f530eeb11ccee5faec2829032711ca02be3fdd827bf98` | 可替换集成/未来可视化夹具 |

## 工具链、依赖与许可证

- 验收工具：Node `24.18.0`、npm `11.16.0`、Git `2.51.0`。
- 模块与全部 workspace 均为 `private: true`、`UNLICENSED`。
- Creator 直接依赖保持 React `19.2.7`、React DOM `19.2.7`、Vite `7.3.5`、`@vitejs/plugin-react` `5.2.0`、TypeScript `5.8.3`、`@types/react` `19.2.17`、`@types/react-dom` `19.2.3`。
- Validator 新增 Ajv `8.20.0`（MIT）与 jsonc-parser `3.3.1`（MIT）；Ajv 传递依赖均为 MIT 或 BSD-3-Clause，精确表见 `docs/DEPENDENCIES_AND_LICENSES.md`。
- `caniuse-lite@1.0.30001807` 的 CC-BY-4.0 仅沿用用户已批准的 Creator 间接开发依赖例外。

## 验证证据

| 检查 | 状态 | 命令 | 结果 |
| --- | --- | --- | --- |
| 依赖树 | 通过 | `npm.cmd ci`、`npm.cmd prefix`、`npm.cmd ls --all` | 干净安装 78 个包；prefix 为模块根；无 missing/extraneous |
| 环境诊断 | 通过并有预期 warning | `npm.cmd run --silent doctor -- --json` | R1 必需工具 ready；Godot 缺失为后续可选 warning |
| Godot 严格门 | 预期失败 | `node scripts/doctor.mjs --strict-godot --json` | 退出 1；未伪装为已就绪，也不阻塞 R1 |
| Pack 聚合 | 通过 | `npm.cmd run verify:pack` | 合同 5、Validator/CLI 39、样例负向 23 项通过；2/2 样例合法 |
| 模块门禁 | 通过 | `npm.cmd run verify` | 7 个步骤；158 项 Node 测试、Creator build 与 loopback smoke 通过 |
| R1 范围 | 通过 | `npm.cmd run check:round-scope` | Creator 与父仓变更均被拒绝；本轮差异合法 |
| 父仓范围 | 通过 | `npm.cmd run check:parent-scope -- --base 8deeebb85d2db1b7f1b3564fca984503ce5787a2` | 固定基线之外仅模块路径变化 |
| 父前端基线 | 通过 | 父 `client` 中 `npm.cmd ci`、`npm.cmd run build` | 3046 个模块构建完成；源码、manifest、lock 零差异 |
| 独立拆分 | 通过 | `npm.cmd run verify:extraction` | standalone `npm ci`、依赖树、完整 verify、smoke 与 source archive 通过；最终哈希见仓外交付清单 |
| 完整差异 | 通过 | `git diff --check`、路径与 Creator 零差异审计 | 全部变更仅在模块内；无跟踪生成物或秘密 |
| npm audit | 已记录 | `npm.cmd audit --json` | 仅既有 `esbuild@0.27.7` low；无 moderate/high/critical |

首次拆分预演暴露 Windows `core.autocrlf` 会把 standalone 样例检出为 CRLF，导致工作树 SHA 漂移。R1.5 因此新增模块根 `.gitattributes`，将文本固定为 LF、常见二进制保持 binary，并加入模拟 `core.autocrlf=true` 的回归测试；最终拆分在该护栏下重新执行。首次失败日志按 harness 保留在仓外临时目录。

## 未运行项及原因

- 未运行后端测试：R1 对 `server/**`、API、数据库和公共类型零修改。
- 未运行 Docker 或共享栈：R1 不含服务集成，且用户要求任何共享栈重建前另行确认时间窗口和基线。
- 未运行 Godot 工程：R1 明确不创建 Godot 项目；严格 doctor 已如实证明工具尚未就绪。
- 未做样例浏览器/3D 可视化：R1 冻结 Creator 且不实现 Inspector，样例只通过 JSON、CLI 与结构测试验收；这避免用题材打磨挤占引擎主线。

## 人工验收清单

- [ ] 确认父 `/matrix-oasis` 占位页、父路由和 Creator R0 空壳均未变化。
- [ ] 在模块根复跑 `npm.cmd ci`、`npm.cmd run verify:pack` 和 `npm.cmd run verify`。
- [ ] 查看两个 JSON：中性样例是机制权威；末班地铁样例只有低保真结构，不代表最终题材或成品物料。
- [ ] 确认替换末班地铁文件不会要求修改 Schema、Validator 或公共诊断。
- [ ] 核对仓外交付的最终 HEAD、standalone tree、archive SHA-256 与拆分命令。
- [ ] 明确回复“R1验收通过，可以创建PR”后，才进入主线差异复核、同步决策与 PR 流程。

## 硬门与回退

- 用户明确回复“R1验收通过，可以创建PR”前不 push、不创建 PR。
- 批准后任何源码、合同、样例、lockfile、脚本或文档变化都会使批准失效。
- 主线前进时先报告差异，不擅自 rebase；冲突解决后全量重验并重新人工确认。
- 不重建或复用共享栈；任何未来操作必须先确认时间窗口与共享基线。
- R1 提交可逆序 `git revert`；R1 无数据库、路由、环境变量、Godot 或运行数据需要恢复。
