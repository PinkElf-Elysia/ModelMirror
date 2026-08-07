# 依赖与许可证策略

## 模块许可状态

- 模块根与所有内部 workspace：`private: true`。
- 模块许可证：`UNLICENSED`。
- 用途：仅内部实验，不发布 npm 包，不授予再分发许可。

`UNLICENSED` 是发布与分发约束，不代表第三方依赖没有各自许可证。每个依赖仍须保留其上游许可义务。

## 依赖准入

无需额外人工审批的许可证仅限：

- MIT
- Apache-2.0
- BSD-2-Clause
- BSD-3-Clause
- ISC

任何其他许可证、双重许可、未知许可或自定义条款必须在引入前提交人工审批。依赖必须使用精确版本并写入模块自己的 lockfile；不得依赖父仓 manifest、lockfile 或 `node_modules`。

## 当前直接依赖

R0.2 已固定以下 Creator 直接依赖；R1.2 新增的合同 workspace 不含第三方依赖：

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| React | 19.2.7 | MIT | Creator 视图 |
| React DOM | 19.2.7 | MIT | 浏览器渲染 |
| Vite | 7.3.5 | MIT | 构建与 preview |
| @vitejs/plugin-react | 5.2.0 | MIT | React 转换 |
| TypeScript | 5.8.3 | Apache-2.0 | 类型检查 |
| @types/react | 19.2.17 | MIT | React 类型 |
| @types/react-dom | 19.2.3 | MIT | React DOM 类型 |
| Ajv | 8.20.0 | MIT | JSON Schema 2020-12 严格结构验证 |
| jsonc-parser | 3.3.1 | MIT | 严格 JSON 语法树与重复键定位 |

测试与护栏仍使用 Node 24 内置 `node:test`，不引入 Vitest、Testing Library、Tailwind、路由器或 UI 库。Ajv 与 jsonc-parser 仅由模块内验证器 workspace 使用；验证过程无网络、无代码生成落盘、无父仓依赖。

Ajv 8.20.0 的新增传递依赖已按模块 lockfile 盘点：

| 依赖 | 版本 | 许可证 | 来源 |
| --- | --- | --- | --- |
| fast-deep-equal | 3.1.3 | MIT | Ajv 间接依赖 |
| fast-uri | 3.1.5 | BSD-3-Clause | Ajv 间接依赖 |
| json-schema-traverse | 1.0.0 | MIT | Ajv 间接依赖 |
| require-from-string | 2.0.2 | MIT | Ajv 间接依赖 |

以上均属于既有许可证准入范围，不新增例外。版本变化时必须重新盘点。

当前 `npm audit` 对 Vite 的间接开发依赖 `esbuild@0.27.7` 报告 1 个 low severity 项（`GHSA-g7r4-m6w7-qqqr`，Windows 开发服务器场景）。R1.1 不自动升级用户锁定的工具链；开发与 preview 只允许绑定 loopback，后续升级前须重新审计和审批。

## 人工批准的许可证例外

| 包 | 版本 | 许可证 | 范围 | 审批状态 |
| --- | --- | --- | --- | --- |
| caniuse-lite | 1.0.30001807 | CC-BY-4.0 | `@vitejs/plugin-react` 经 Babel/Browserslist 引入的间接开发依赖 | 用户于 2026-08-06 在 R0 实施任务中明确批准 |

该例外只适用于上述精确包与版本，不扩展 CC-BY-4.0 的通用准入范围。若分发依赖材料，必须保留上游归因与许可证通知；版本变化后需要重新盘点并审批。

模块根与合同 workspace 的版本标识均为 `0.1.0-r1`；冻结的 Creator workspace 保持 `0.0.0-r0`。三者均为 private/UNLICENSED，不代表发布版本。

## 变更流程

新增或升级依赖时必须：

1. 记录精确版本、直接/间接用途和许可证；
2. 更新模块 lockfile；
3. 运行 `npm ci`、`npm ls --all` 和完整验证；
4. 重新执行拆分验证；
5. 若许可证不在准入清单，先取得人工批准。
