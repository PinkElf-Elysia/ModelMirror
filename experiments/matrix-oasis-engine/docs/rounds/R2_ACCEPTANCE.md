# R2 验收记录

状态：R2.5 自动验证、独立拆分与浏览器验收证据已收口，等待用户最终人工验收

固定基线：`a8e627e217c8c9e2cb8cca83fea8542c47edaeba`

最终 HEAD、split tree 与 archive SHA-256 只记录在仓外交付清单，避免本文自引用。

## 成功定义

- [x] 全部 R2 变更位于 `experiments/matrix-oasis-engine/**`。
- [x] R1 contracts、validator、examples 与 R0/R1 验收记录相对固定基线零差异。
- [x] 参考模拟器完整执行 R1 condition、effects、Cue 与 typed target 语义，并保持确定性与原子回滚。
- [x] Creator 可在无父服务时运行两个内置夹具和本地合法 JSON，非法候选不会破坏当前会话。
- [x] `npm run verify`、固定范围检查与历史保留型拆分全部通过。
- [x] 父源码、Matrix Oasis 页面、配置与共享栈零改动。
- [ ] 用户完成最终人工验收。

## 批次

| 批次 | 目标 | 提交 | 状态 |
| --- | --- | --- | --- |
| R2.1 | 治理、正向 allowlist 与 R1 冻结 | `1b259b7` | 已完成 |
| R2.2 | 浏览器兼容的确定性参考模拟器 | `590449a` | 已完成 |
| R2.3 | 运行语义与夹具轨迹 | `36f5c7a` | 已完成 |
| R2.4 | Creator 最小运行实验台 | `28ea431` | 已完成 |
| R2.5 | 拆分证据与人工验收包 | 本批次提交，最终 SHA 见仓外交付清单 | 已完成，待用户验收 |

## 固定边界

- 父项目交互、网络、Godot、二进制与秘密策略保持不变。
- R2 不实现 Compiler、Runtime Pack、存档、批量 replay、AI、3D、父接入、Docker、CI 或部署。
- 不重建或复用共享栈；任何例外必须先确认时间窗口与共享基线。
- 用户明确回复“R2验收通过，可以创建PR”前不 push、不创建 PR。

## 证据模板

每批记录目标、精确 diff、命令与退出码、测试数量、提交 SHA、风险和回退。最终 HEAD、standalone tree 与 archive SHA-256 在 R2.5 提交后的仓外交付清单中记录。

### R2.1 验证摘要

- `npm.cmd ci --no-audit --no-fund`：退出 0，安装 78 个包；仅保留既有 `esbuild@0.27.7` install-script warning。
- `npm.cmd prefix`：退出 0，指向模块根。
- `npm.cmd ls --all`：退出 0，无 missing 或 extraneous。
- `npm.cmd run check:round-scope`：退出 0，正向 allowlist 与冻结路径检查通过。
- `npm.cmd run check:parent-scope -- --base a8e627e217c8c9e2cb8cca83fea8542c47edaeba`：退出 0，父仓范围检查通过。
- `npm.cmd run verify`：退出 0，167 项测试通过；Creator 构建与 loopback 冒烟通过；Godot 缺失如实报告为非阻塞 warning。
- R1 contracts、validator、examples 与 R0/R1 验收记录相对固定基线零差异。

### R2.2 验证摘要

- 新增 private/UNLICENSED 的 `@matrix-oasis/game-pack-simulator@0.1.0-r2`；只依赖冻结的内部 Validator，不增加第三方依赖。
- `npm.cmd ci --no-audit --no-fund`：退出 0，安装 79 个包；仅保留既有 `esbuild@0.27.7` install-script warning。
- `npm.cmd ls --all`：退出 0，simulator workspace 链接与内部依赖完整，无 missing 或 extraneous。
- `npm.cmd run verify:simulator`：退出 0，12 项核心模拟器测试通过。
- `npm.cmd run verify`：退出 0，179 项测试通过；Creator 构建与 loopback 冒烟继续通过。
- `npm.cmd run check:boundary` 与 `npm.cmd run check:round-scope`：退出 0；模拟器无父依赖、网络、持久化或题材专属实现。
- R1 冻结路径相对 `1b259b7` 零差异；本批不修改 Creator。

### R2.3 验证摘要

- 以题材中性的 mechanics conformance 夹具固定五步权威轨迹，精确断言变量、位置、步数、transition 与 Cue 顺序；同一输入完整执行 20 次，序列化结果逐字节一致。
- 九种 condition、三种 effect 与两种 typed target 均有执行证据；比较符严格/含等号边界、`all`/`any`/`not` 短路、连续 `set`/`add`、正负安全整数边界与中间溢出原子回滚均已单独锁定。
- 创建、effect、node entry 与 ending 的多 Cue 声明顺序及重复发射已锁定；循环、停滞、ending、未知/不可用 action 与精确 step limit 行为均已覆盖。
- Snapshot 门覆盖根与嵌套字段、Pack 身份、状态/位置耦合、步数范围、变量缺失/额外/错型、JSON round-trip 与无敏感值诊断；inspection、failure 与 public result 均保持冻结。
- 末班地铁薄型夹具无需题材特判即可到达三个 ending 并验证显式循环；模拟器运行源码无 examples 导入或题材专属 ID、文案与关键词。
- `npm.cmd run verify:simulator`：退出 0，29 项模拟器核心与语义测试通过。
- `npm.cmd run verify`：退出 0，196 项测试通过；Creator 构建与 loopback 冒烟继续通过，Godot 缺失仍如实报告为非阻塞 warning。
- `npm.cmd run check:boundary`、`npm.cmd run check:round-scope` 与 `git diff --check`：退出 0；R1 冻结路径和 Creator 相对 `590449a` 零差异。
- 本批仅补齐参考模拟语义和测试，不实现 Compiler、Runtime Pack、存档或题材功能；内部 condition test seam 未通过 package root、类型声明或 exports map 暴露。
- 回退本批提交后，R2.2 的公开模拟器核心仍可独立使用。

### R2.4 验证摘要

- Creator 默认加载题材中性的 mechanics fixture，并可切换 integration fixture；所有样例标题、摘要、正文和状态均来自 Pack inspection，界面没有题材条件分支。
- 本地文件入口只在浏览器内存处理 `.json`，执行读前/读后 1 MiB、实际字节长度、fatal UTF-8 与异步 token 检查；失败或 stale 结果保持原 active session 引用，成功候选冻结后一次性提交且不保存文件名。reset/action 也以同一 base session 计算完整候选，并以引用 CAS 拒绝迟到结果，避免 prepared 与 snapshot 混合。
- 实验台展示当前 node/ending、正文、可用与不可用 actions、变量、步数、最近 transition 与本步 Cue；只提供单步执行和重置，不提供编辑、保存、导出、回放、自动运行或 step-limit UI。
- 保留 `MATRIX_OASIS_R0_ISOLATED_SHELL`，新增 `MATRIX_OASIS_R2_REFERENCE_SIMULATOR`；原生控件、文字状态、`aria-live`、成功后位置标题焦点、44px 目标与 320px 响应式护栏已自动检查。
- `npm.cmd run test:creator`：退出 0，19 项 Creator shell、本地文件与会话事务测试通过；模拟器 operational throw 映射为静态 `PACK_RUNTIME_INTERNAL_ERROR` 且不泄漏底层异常。
- `npm.cmd run verify:creator`：退出 0；TypeScript、Vite production build（227 modules）与双标识 loopback smoke 通过。
- `npm.cmd ls --all`：退出 0；Creator 只新增 simulator 内部 workspace 链接，无 missing、extraneous 或新第三方依赖。
- `npm.cmd run verify`：退出 0，212 项测试通过；全部固定步骤、Creator build 与 loopback smoke 通过，Godot 缺失仍是非阻塞 warning。
- `npm.cmd run check:boundary`、`npm.cmd run check:round-scope` 与 `git diff --check`：退出 0；R1 冻结路径相对 `36f5c7a` 零差异，父仓与共享栈未触碰。
- 独立 dev 预览曾返回 HTTP 200 并已终止；仓外截图、桌面/移动浏览器人工验收、三种 ending 与本地文件交互验收留在 R2.5，未提前标记完成。
- 新增 `docs/PRODUCT.md` 固定 product register、用户、用途、反例与可访问性原则；未新增设计依赖、根外配置或共享服务。
- 回退本批提交后，R2.3 参考模拟语义与测试仍保持完整。

### R2.5 验证摘要

#### 工具链、依赖与自动门禁

- 固定基线为 `a8e627e217c8c9e2cb8cca83fea8542c47edaeba`；R2.1 至 R2.4 依次为 `1b259b7`、`590449a`、`36f5c7a`、`28ea431`，均为线性本地提交。
- Node `24.18.0`、npm `11.16.0`、Git `2.51.0.windows.2`；`npm.cmd ci --no-audit --no-fund` 退出 0，安装 79 个包，仅出现已知 `esbuild@0.27.7` install-script review warning。
- `npm.cmd prefix` 精确指向模块根；`npm.cmd ls --all` 退出 0，无 missing 或 extraneous。R2 只增加内部 simulator workspace 依赖，没有新增第三方许可；既有 `caniuse-lite@1.0.30001807` CC-BY-4.0 仍限开发期传递依赖并沿用已批准例外。
- `npm.cmd run doctor` 与纯 JSON 模式均退出 0，状态为 `ready_with_warnings`；Godot 未安装如实报告为后续可选 warning。`npm.cmd run doctor:godot` 按预期退出 1，没有被伪装成 R2 已就绪。
- `npm.cmd run verify:pack` 退出 0：5 项 contract、39 项 Validator/CLI、23 项样例测试通过；`npm.cmd run verify:simulator` 退出 0，29 项模拟器测试通过。
- `npm.cmd run verify` 退出 0：212/212 项测试通过，Creator production build 转换 227 modules，loopback smoke 返回 HTTP 200 并同时命中 R0/R2 两个稳定标识。
- `npm.cmd run check:boundary`：`checked=84 tracked=84`；`npm.cmd run check:round-scope` 与固定基线 `check:parent-scope`：`checked=38 changed=38`，均退出 0。
- 相对固定基线的 38 个变更路径全部位于模块前缀；父 `client/**`、`server/**`、`.github/**`、Docker、根 manifest/lock、公共文档与现有 Matrix Oasis 文件零差异。全部 R1 冻结路径逐字节零差异。

#### 历史保留型拆分与父仓无回归

- 在 R2.4 HEAD 上执行 `npm.cmd run verify:extraction` 退出 0；standalone 根包含 84 个跟踪文件，从空依赖完成自身 `npm ci`、全量 `verify`、Creator build/smoke 与 archive 校验，临时仓和归档由脚本安全清理。
- R2.5 文档提交后必须再执行一次最终 extraction；最终 HEAD、split tree 与 archive SHA-256 只写入仓外交付清单，避免本文自引用。
- 父 `client` 在同一隔离 worktree 内执行 `npm.cmd ci --no-audit --no-fund` 退出 0，安装 384 个包；`npm.cmd run build` 退出 0，转换 3047 modules。仅保留既有大 chunk warning，构建后 `git status --short -- client` 为空。
- Windows 隔离环境中，首次清理既有 Creator `dist` 曾因本轮预览残留进程和 `C:\\tmp` 沙箱权限出现 `EPERM`；仅在核对进程加载模块路径后结束 R2 自有预览进程，并把忽略生成目录移到唯一临时隔离路径。获得同一 worktree 的临时目录权限后，全量 build 与 verify 均通过，跟踪文件未变化。

#### 独立浏览器验收

- 只启动模块自己的 Vite 预览 `http://127.0.0.1:4193/`，未启动父前后端、Docker 或共享栈；验收结束后端口已释放。
- mechanics 中性夹具按五步权威轨迹到达 `Pass` ending；界面逐步显示变量、transition、不可用 action 与重复 Cue，成功 action 和 reset 后焦点落到 `location-title`。
- 末班地铁薄型夹具无需题材分支分别到达 `带回一个版本`、`留在空格里` 与 `再次抵达十三站` 三个 ending；每条路径前重置会话后均从 Step 0 独立运行。
- 本地合法 mechanics JSON 通过 fatal UTF-8 与 Validator 后原子替换会话；非法模块 manifest、9 MiB 超限 `.json` 与 111682-byte 非 UTF-8 `.json` 分别被安全拒绝，当前 `mechanics-conformance` Step 0 会话保持不变，稳定码包括 `PACK_LOADER_FILE_TOO_LARGE` 与 `PACK_LOADER_UTF8_INVALID`。
- 375px 浏览器验收中所有可见交互目标不小于 44px，document/body 无横向溢出；320px 下 `scrollWidth` 精确等于 320 且没有元素越界。桌面 1280px 下 document `scrollWidth=1265`、viewport `1280`，同样无横向溢出。
- 浏览器 warning/error 日志为 0；页面资源声明只包含同源 loopback Vite client 与 `src/main.tsx`，页面无 `/api` 文案，机器边界同时禁止 Creator 网络能力。桌面、末班地铁 ending 与移动端截图均只存仓外。
- Browser 自动化能够确认原生按钮、Tab 焦点落点、成功后的标题焦点与 focus ring；当前工具的合成 Enter 不触发浏览器默认按钮动作，且不暴露完整 Network 面板。因此最终用户验收仍需手工复核 Enter/Space 激活和 Network 面板零父 API，这两项未被伪装为已人工通过。
- 默认 `stepLimit=256` 下的零可用 action 停滞不做 256 次浏览器点击；等价的精确 step-limit、零可用 action 与 reset 语义已由 29 项 simulator 测试和 Creator 静态/事务测试覆盖。

#### 未运行项、风险与回退

- 未运行后端测试、Docker、共享栈、部署或父路由冒烟，因为本轮对这些路径零修改；没有请求共享栈时间窗口。
- 未运行 Godot headless；Godot 仍是后续可选工具且本轮禁止创建项目。
- 已知依赖风险保持为既有 `esbuild` low severity 与批准的开发期 CC-BY-4.0 传递依赖；R2 没有新增 registry 依赖或许可证例外。
- 本批只修改本验收记录；逆序 revert R2.5 后不影响模拟器和 Creator，整体回退仍为逆序 revert 五个 R2 提交。

## 回退

批次使用逆序 `git revert`；合并后可整体 revert R2 PR。R2 不产生数据库、父路由、环境变量、共享服务或运行数据恢复工作。
