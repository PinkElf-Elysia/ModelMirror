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

## R0 依赖计划

R0.1 尚未创建 npm 工程。R0.2 将固定以下直接依赖：

| 依赖 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| React | 19.2.7 | MIT | Creator 视图 |
| React DOM | 19.2.7 | MIT | 浏览器渲染 |
| Vite | 7.3.5 | MIT | 构建与 preview |
| @vitejs/plugin-react | 5.2.0 | MIT | React 转换 |
| TypeScript | 5.8.3 | Apache-2.0 | 类型检查 |
| @types/react | 19.2.17 | MIT | React 类型 |
| @types/react-dom | 19.2.3 | MIT | React DOM 类型 |

测试与护栏使用 Node 24 内置能力，不引入 Vitest、Testing Library、Tailwind、路由器或 UI 库。

## 人工批准的许可证例外

| 包 | 版本 | 许可证 | 范围 | 审批状态 |
| --- | --- | --- | --- | --- |
| caniuse-lite | 1.0.30001807 | CC-BY-4.0 | `@vitejs/plugin-react` 经 Babel/Browserlist 引入的间接开发依赖 | 用户于 2026-08-06 在 R0 实施任务中明确批准 |

该例外只适用于上述精确包与版本，不扩展 CC-BY-4.0 的通用准入范围。若分发依赖材料，必须保留上游归因与许可证通知；版本变化后需要重新盘点并审批。

## 变更流程

新增或升级依赖时必须：

1. 记录精确版本、直接/间接用途和许可证；
2. 更新模块 lockfile；
3. 运行 `npm ci`、`npm ls --all` 和完整验证；
4. 重新执行拆分验证；
5. 若许可证不在准入清单，先取得人工批准。
