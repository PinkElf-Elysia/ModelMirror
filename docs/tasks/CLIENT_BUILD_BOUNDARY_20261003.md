# CLIENT-BUILD-BOUNDARY：独立修复前端镜像类型检查边界

## 开工范围

- 基线：`f34aca06e3e494d42c40ff6ebff22f24bd637bfc`，开工时工作树干净。
- 分支：`codex/client-build-boundary-20261003`。
- 工作树：`C:\tmp\modelmirror-client-build-boundary-20261003`。
- 开工时用户仅批准独立修复主线打包问题，不混入 R7 功能、不提交、不部署；后续提交授权另记下文。
- 单一目标：`client/` 独立构建上下文可以构建生产镜像，完整 CI 类型检查仍覆盖测试代码及其 RPG 源码依赖。
- 允许修改四个文件：本任务卡、`client/package.json`、`client/tsconfig.build.json`、`client/scripts/build-typecheck.node.mjs`。
- 不改 Docker 上下文、RPG、运行代码、后端、R7、CI 权限、锁文件或依赖。无 API、数据库和数据迁移变化。

## 已证实依据

1. `docker-compose.yml` 的前端构建上下文为 `./client`；Dockerfile 执行 `npm run build`。
2. `build` 和 `typecheck` 原来都使用 `tsc -b`，`tsconfig.app.json` 包含全部 `src`，测试引用了 `experiments/ai-rpg-engine/card-replica/src`。
3. `4932e3b4` 干净主线和 R7 集成树实际镜像构建都失败，12 条唯一 `TS2307` 完全一致；`4932e3b4..f34aca06` 没有更改相关源码或构建文件。
4. CI 已独立执行完整 `typecheck`，并安装锁定的 RPG 前端依赖；这两项必须保留。
5. TypeScript 官方文档确认 `extends` 保留基配置、`exclude` 只过滤根文件而不能绕过导入文件检查、`tsc -b` 可以显式指定多个配置。参考：[Project References](https://www.typescriptlang.org/docs/handbook/project-references.html)、[exclude](https://www.typescriptlang.org/tsconfig/exclude.html)。

## 实施与风险

生产配置继承全部现有严格选项，仅排除测试根文件和测试初始化目录，使用独立增量缓存；构建继续检查 Vite 配置。`npm run typecheck` 及完整应用配置保持不变。新反例测试加入现有 `test:run`。

风险是错误地把生产代码排除，或将测试错误从 CI 中隐藏。测试必须对比完整/生产根文件集合和编译选项，并验证：测试类型错误被完整检查拒绝、生产错误被两条路径拒绝、生产导入测试文件时仍拒绝、仓库外测试依赖只在完整检查中解析。

验证仅操作本任务隔离工作树、临时目录和专用一次性容器。可使用锁文件依赖和公开基础镜像；不读取凭据、不调用模型、不访问业务数据、不操作共享栈。

## 验证矩阵

| 检查 | 命令或步骤 | 状态 |
| --- | --- | --- |
| 边界与反例 | `cd client; node --test scripts/build-typecheck.node.mjs` | 通过，6 项 |
| 完整类型检查 | `cd client; npm.cmd run typecheck` | 通过，完整应用与测试配置没有改动 |
| 前端回归 | `cd client; npm.cmd run test:run` | 通过，149 个 Vitest 文件 / 1,109 项；随后 Node 检查 7 项 |
| 生产构建 | `cd client; npm.cmd run build` | 通过；保留既有大于 4,500 kB 的 chunk warning |
| 独立镜像构建 | `docker build --pull=false -f client/Dockerfile -t modelmirror-client-build-boundary:20261003 client`，空凭据配置 | 通过，实际 `./client` 上下文，无 Dockerfile 修改 |
| 镜像安全冒烟 | 专用无网络、无主机端口/卷、只读、非 root 容器 | 通过，四条页面路径、JS/CSS、空运行配置；镜像无源码和测试依赖 |
| Diff 与敏感信息 | `git diff --check`、四文件范围、已知凭据签名检查 | 通过，零签名命中、锁文件零改动；不等于全面安全审计 |
| 后端全量 | 本批没有后端改动；R7 全量在另一个冻结工作树独立执行 | 不适用 |

### 证据与失败保留

- Node 24.18.0，锁定 TypeScript 5.8.3；前端和 RPG 测试依赖均通过 `npm ci --offline --ignore-scripts --no-audit --no-fund` 安装。
- 本地 `test:run` 的 Vitest 耗时 413.09 秒；本地 Vite 构建耗时 16.36 秒。
- 首次增加 `--network=none` 的镜像构建失败于 `npm ci`：包脚本变化使原依赖层缓存失效，没有网络取回依赖。该失败保留为 `client-image-build.log`，未改依赖或跳过安装。随后按原 Dockerfile 取回锁定公开依赖的构建成功，记录为 `client-image-build-v2.log`。
- 成功镜像 manifest 为 `sha256:66c2f260d85f4b7fccdc058c038fdad2a7a4a8331ad2e71536acd7d2bff9b1e3`，运行时 Node 22.22.3。
- 容器 `mm-client-build-smoke-20261003` 验证 `/`、`/models`、`/agents/meta-agent`、`/forms/synthetic` 和构建入口 JS/CSS，均为 200；表单安全响应头保留。未调用后端或外部 Provider，不构成产品交互或 R7 验收。
- 日志和生成产物只在忽略目录 `.tmp-client-build-20261003/`、`client/dist/` 和 `node_modules/`，不纳入交付。
- GitHub CI 未运行；本批无提交或远程变更。R7 集成树仍保留原构建失败证据，没有将此独立补丁自动混入。

## 回退与完成边界

回退本批的构建脚本、独立配置和反例测试即可恢复原构建流程，不涉及持久化数据。回退后完整类型检查仍须执行；已存在的镜像打包问题会重新出现。

Help Center Impact: None。仅调整开发构建入口的类型检查根集合；不改变路由、界面、用户操作、运行行为或帮助步骤。生产源码类型校验、完整 CI 校验均保留。

首批状态：独立修复及本批本地验证完成；当时未 Commit、Push、PR、Merge、Deploy，不据此宣布 R7 验收或 R8 前置完成。

## 提交前核验

2026-10-03 用户要求“收尾门禁后优先提交再进入 R8”，允许通过对应门禁后提交和创建 PR，不包含远程合并、部署、真实模型调用或 R8 实施。

只将本独立分支 fast-forward 到 `2c82d8a53c52fdec507ec5f81ffec5ebc577c750`。新增五个 Provider 验收工具/测试路径与本修复四文件无交集，未改变前端、Dockerfile、锁文件或现有验证结果的运行源码。提交前复查六项反例、强制完整类型检查、生产构建、范围和敏感签名；仍只提交本独立四文件，不夹带 R7。

R7 使用本补丁的组合镜像已有独立构建及断连不重发冒烟证据，但其人工、真实模型、提交和合并门禁不由本 PR 代替。

最新基线复查已完成：六项反例全部通过；`npm.cmd run typecheck -- --force` 通过；`npm.cmd run build` 通过，Vite 15.31 秒，保留既有 chunk warning。原全量前端、镜像与冒烟证据绑定相同前端源码和锁文件，不改标为新运行。GitHub CI 待提交后实际核验，不预先声称通过。
