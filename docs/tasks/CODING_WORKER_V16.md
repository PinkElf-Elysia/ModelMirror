# V16 OpenCode 等效门禁与受控多 Agent 任务卡

## 基线与宣传边界

- 开工基线：合并 PR #173 后的 `origin/main@dce62ee54c033f1b0b55f69f807c7c6b4bb91df5`；发布前因主线前进，PR A 已无冲突重放到 `origin/main@09f4cca4f1e02fe275ada17535597437cac3778d`，最终分支相对该基线仅包含六个 V16 提交。
- PR A 只实现真实会话与等效 Harness；PR B 的单 Agent 能力补齐和 PR C 的 Subagent 均不提前混入。
- 在两轮完整真实对照通过前，功能保持 Experimental、默认关闭，禁止使用“接近原版 OpenCode”“等同 OpenCode”或“完整替代 OpenCode”。
- 最终唯一允许的表述是：“在已验收的受控 Python/TypeScript 仓库开发任务上，ModelMirror Coding Worker 的任务成功率与恢复能力接近 OpenCode 1.18.9。”

## PR A 文件与提交边界

每个逻辑提交不超过五个文件：

1. 冻结 24 项任务清单、隐藏检查摘要、144 格运行矩阵、确定性报告判定和两轮认证。
2. Provider 私有契约 v3：统一 plan、todo、工具边界、usage、question、compaction 与错误分类。
3. 规范化会话台账：只保存公开消息、计划、todo、工具摘要、operation ID、检查证据和完整边界，不保存隐藏思维链。
4. 活动预算：只累计 preparing/running/testing，跨重启不重置；queue、approval、user-input 等待不计入。

## 冻结对照规则

- 任务清单固定为 Python、TypeScript/React、仓库开发、会话与协作四类，每类六项，每项对两侧各运行三次。
- 两侧使用相同的受控模型路由回执、目标、初始 tree、隐藏检查、时间、轮次和输出预算。
- timeout、budget limited、卡死、人工修复、未声明副作用、策略违规或错误最终 tree 一律计为失败。
- CI 只验证清单、结果模式、判定算法和 Fake runner；真实模型对照只在人工发布窗口运行。
- 报告只保存公开输出和统计，不保存密钥、隐藏检查正文、供应商原始帧或隐藏思维链。

## 量化门禁

- Worker accepted-run 成功率至少 85%，且落后 OpenCode 1.18.9 不超过 5 个百分点。
- 每个类别至少 80%；OpenCode 达到 2/3 的任务，Worker 不得 0/3。
- 安全、原子性、跨任务隔离和重启唯一性用例必须 100%。
- Worker 中位活动时长与 token 均不超过基线 1.5 倍；批准等待时间不计入活动时长。
- 同一 candidate SHA 和冻结 manifest 必须连续两轮完整通过。
- 发布窗口同时记录最新 OpenCode 版本的差距审计；固定 1.18.9 不能用于隐藏新增能力差距。

## 自动与人工证据分离

自动门禁记录命令、计数和环境；Mock/Fake 只证明确定性协议，不证明真实模型成功率。人工窗口需保存版本、candidate SHA、manifest hash、tree hash、检查结果、tokens、工具调用、活动时长和失败分类，并在 Console 脚本化验收无 P0/P1 后才能决定是否采用限定宣传文案。

## 回退

关闭 V16 对应开关后，新任务回到 V15；已有 V16 任务进入 `interrupted`，保留 Store、Workspace、Evidence 与 v13 Recovery。不得自动重放工具副作用或删除用户数据。

## PR A 验证快照

- Worker Linux 专项：`170 passed, 5 skipped`；Windows 本机同组有三个未修改的 Host Snapshot 文件身份基线差异，Linux 对应文件 `7 passed`。
- Agent Workspace 与 Coding 联合回归：`837 passed, 14 skipped`。
- 后端全量：`2909 passed, 29 skipped, 2 failed`；两项失败均可在 PR A 零 Diff 的主线路径复现或由既有容器环境解释：Linux overlay 上 Host Apply 同内容替换的文件身份复用，以及旧 server 镜像 Node 20 直接加载 TypeScript 失败（宿主 Node 24 对应测试通过）。
- 前端：生产 build 通过；测试 `233 passed, 1 failed`，失败为 PR #176 新增 `vision_understanding` 后主线 `NodePalette` 旧期望未同步，PR A 无前端 Diff。
- Compose：V14 + V15 Claude overlays 的 `config --quiet` 通过；Python `py_compile`、`git diff --check`、敏感信息与禁止产物扫描通过。
- 真实 144 次模型对照和两轮认证尚未运行，因此 PR A 保持 Draft/Experimental，不能使用限定或非限定的“接近 OpenCode”宣传文案。

## PR B 实现与验证快照

PR B 基于 PR A `e03f5987`，按十个独立逻辑提交实现，且每个提交均不超过五个文件：

1. `4126254f`：可恢复的结构化计划、一次性问题回答与 `waiting_input`。
2. `8f628cab`：只读取 H0 中哈希绑定、限量且分层的仓库说明。
3. `2bca2623`：以平台包装消息向 OpenCode 与 Claude 注入同一受控仓库说明。
4. `801c7fe4`：仅在完整工具边界执行并由 Worker 生成的受控上下文压缩。
5. `85eb2848`：二进制安全、精确 tree hash 绑定的持久回合快照。
6. `eb52b82c`：无活动命令、服务、审批或问题时可用的原子 undo/redo。
7. `99caea20`：从精确公开回合快照创建隔离任务 fork，不继承 Provider 会话、审批、租约或 operation。
8. `08a5b6bd`：开放 plan、questions、undo/redo/fork、children 与脱敏 export 公共接口。
9. `62f33588`：冻结 `npm ci`、`uv --frozen`、带 SHA-256 哈希的 requirements，以及官方资源目录驱动的双租约文档出站。
10. `f1da6dbe`：把冻结依赖与文档查询接入真实 Provider MCP/RPC 边界，并保持 URL、租约与供应商信息不可由模型直接提交。

自动门禁结果：

- 全部 Coding Worker：`187 passed, 5 skipped`；MCP/RPC、Tool Broker 与 Provider 合同的新增定向组合为 `33 passed`。
- Agent Workspace 与 Coding 联合回归：`852 passed, 14 skipped, 1 failed`；唯一失败为只读 bind/overlay 上 Host Apply 同内容文件替换复用了文件身份，PR B 对该路径零 Diff。把源码复制到容器内正常可写 overlay 后，对应 Host Apply 用例通过。
- 后端全量（一次性可写源码副本、容器无网络）：`2915 passed, 29 skipped, 12 failed`。其中 11 项 Agency Worker bridge/execution 因现有 `modelmirror-server` 镜像缺少已构建 worker 产物而失败；精确复跑返回 `Agency worker build output is unavailable`。另 1 项为 Node 20 无法直接导入 TypeScript；同一用例在本地 Node 24 测试镜像中 `1 passed`。这些文件与 PR B 均无 Diff。
- 前端：production build 通过；稳定复跑为 `233 passed, 1 failed`，失败仍是主线 `NodePalette` 未把 `vision_understanding` 纳入旧期望。首次全跑另出现一次 OCR 面板查询超时，立即复跑未重现，按测试波动保留记录，不写成已修复。
- V14 + V15 Claude overlays 的 Compose `config --quiet` 通过；20 个变更 Python 文件 AST 解析、逐提交五文件门禁、`git diff --check` 均通过。

上述结果只证明 PR B 的确定性契约、恢复、隔离与默认关闭边界。真实 OpenCode/Claude 对照、24 项任务各三次、连续两轮认证、Console 人工验收以及“接近 OpenCode”限定文案授权仍未完成；PR B 必须保持 Draft/Experimental。

## PR C 实现与验证快照

PR C 基于已验证的 PR B `129c4c08`，按十二个独立提交实现，所有提交均不超过五个文件：

1. `24c596ec`：深度一、每父任务最多四项的加密子任务契约与持久关系。
2. `db03fb00`：父任务停车释放槽位，子任务在另一隔离 Workspace Fork 中排队执行。
3. `c0465fdb`：只读 `explore/review` 与可变更 `implement` 工具策略，不继承父审批、租约、operation、Artifact 或 Provider session。
4. `383d21fd`：公开中立 capability、创建与 children 查询接口。
5. `88eaee84`：绑定 fork H0/result/changed-path receipt 的文本 changeset 合并；父 tree/preimage CAS 冲突时不覆盖父 Workspace。
6. `ddc8b157`、`acd28bc0`、`394a7a04`：稳定 operation ID 的 Provider/MCP/HTTP 合并链与未知回执对账。
7. `52a8ba3a`：公开回合历史查询。
8. `f21c0440`：旧 PR C 数据库的子任务合并字段幂等迁移。
9. `86c3fff6`、`52da3e26`：共享 Console 展示结构化 plan/todo、待回答问题、compaction、turn history、undo/redo/fork、子任务树、合并结果和冲突保护。

自动门禁结果：

- 全部 Coding Worker：`198 passed, 5 skipped`；5 项均为固定 LSP 仅在 Linux Executor 镜像运行的显式 skip。
- Agent Workspace：`44 passed`；第一次只读挂载被 World/MCP 运行时目录初始化挡在收集阶段，改用一次性可写 `server` 副本后通过。
- 全部 Coding：`820 passed, 14 skipped`，另将临时副本遗漏的 `.dockerignore` 精确补入后最后 1 项安全测试通过。临时副本缺 Compose/`.dockerignore` 的 9/1 项失败均已按原测试精确复跑，不属于产品失败。
- 前端：`51` 个测试文件、`235 passed`；production build 通过。Vite 保留既有大 chunk 警告；`npm ci` 报告的 5 项 audit 告警未用无关依赖升级在本 PR 扩面。
- `git diff --check`、逐提交五文件门禁与 Python 定向 `py_compile` 通过。

尚未执行：真实 OpenCode/Claude 子任务、逐组件重启、Host Snapshot 写回、Console 人工脚本验收、144 次真实模型对照及连续两轮认证。因此 PR C 只能以 Experimental、默认关闭交付；不得使用任何“接近 OpenCode”宣传文案。
