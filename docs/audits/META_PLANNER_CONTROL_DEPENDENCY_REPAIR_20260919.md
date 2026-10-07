# CW10 控制依赖修复上下文核对

## 范围与证据

- 基线：`codex/meta-planner-controlled-writes-10-closeout@a7d99925584e818e7f2df84b1646256abfc08ed6`，保留既有未提交变更。
- 修改前核对最近实测的 106 个源码 hash，全部一致。实测来源：`META_PLANNER_TRANSPORT_RETEST_20260919.md`。
- 最近实测三次 completion 均返回完整响应；唯一修复修改了数据边和配置，没有修改控制边。端口错误消失，但 `serialize_query.json -> agent_low_score.task` 的路径可用性及控制祖先错误仍在。
- 原始模型 Graph 没有完整留存，本批夹具是根据安全诊断重建的同类结构，不宣称逐字重放。报告中的 fallback Graph 不作为模型原始 Graph。
- 已证实的信息缺口：现有修复上下文保留完整图和错误，但依赖工作清单只有直接前驱，未暴露权威分析器已经找到的具体反例。不能据此断言这是历次模型失败的唯一原因，亦不能推断模型能力上限。

## 本批允许修改

1. `server/meta_agent/control_flow.py`：从既有失败判定输出只读依赖反例，不改变判定。
2. `server/meta_agent/meta_planner_v2.py`：复用现有校验调用，收集路径与控制祖先事实，送入修复契约。
3. `server/meta_agent/repair_context.py`：以有界清单展示来源、消费者和反例，不生成修复操作。
4. `server/tests/test_meta_planner_control_dependency_repair.py`：离线重建、反例、修复及证伪。
5. 本文：记录红绿测试、验证边界和回退。

禁止新增生产依赖、第二套路径分析、猜测/自动连接控制边、改变授权及运行语义、放宽校验、增加模型调用次数。禁止真实模型调用、预览器重启、业务表读写、Proposal 批准和发布。

## 验收与回退

- 先用新测试证明现有校验拒绝错误结构，且修复 Prompt 缺少结构化反例，再修改生产文件。
- 覆盖互斥分支、公共生产者、分支内生产者、并行但非祖先、资源 error 路径、未知权威类型、截断计数及敏感内容排除。
- 验证数据边/配置局部修改仍失败，显式且语义保持的完整 Patch 才能通过并完成编译往返；同一唯一修复预算不变。
- 使用隔离离线 runner 运行重点回归；语法、前端生产构建和全量入口分别记录，不用局部绿测替代真实模型验收。
- 本批三个生产文件的修改前副本存于独立临时证据目录。回退仅移除本批增量，保留所有既有 CW10 修改和历史证据。

## 执行结果

### 最小改动

- 保留原有路径分析、祖先检查、错误文本和失败门禁；只为失败判定增加内部结构化事实。
- 路径事实给出来源 ref/端口、消费者 ref/输入序号、受影响场景数及一个确定性语义反例。区分“来源节点未执行”和“读取节点已执行但 error 路径没有 result”。不输出实际 witness 值、业务记录或资源正文。
- 控制祖先事实单独标识：两节点均可到达并不等于具有执行先后；该情况不伪造“节点未到达”反例。
- 唯一修复调用复用既有权威校验，从中收集上述事实。模型投影最多展示 64 条，显式记录省略数；不再重复展示同一反例。配置、完整 Graph、授权、任务、Schema 和错误保留。
- 精简重复说明，保持业务语义不变的约束；不增加自动接边、自动改数据来源或额外 completion。

### 离线结果

证据目录：`.tmp-cw10-dependency-repair-20260919/`，独立于全部历史实测目录；属于忽略产物，不提交。

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| 修改前重建与缺口测试 | 1 通过、5 失败 | `red-confirmed.xml`；同类结构原有两项错误复现，合法显式控制修复可编译，失败项对应尚未提供的诊断接口/字段。早期并行夹具的死路已先纠正，不归因于生产代码。 |
| 最终针对性测试 | 117 通过 | `focused-closeout.xml`；新增文件含 18 项证伪用例，其他为既有路径/域分析及修复上下文回归。 |
| 相邻模块回归 | 1,119 通过 | `adjacent.xml`；所有 `test_meta_planner*.py` 加既有 MetaAgent、NodeContract、Authoring、Publish、Evaluation、Evolution、App、typed Workflow 测试。随后新增混合错误用例并精简一处说明限定，最终 117 项再次通过。计数互有重叠，不相加。 |
| 后端语法 | 通过 | 三个生产文件、新测试和 `server/main.py`；字节码在独立临时目录。 |
| 前端生产构建 | 通过 | 未修改客户端；产物在独立目录，不覆盖当前预览器。保留既有大 chunk warning。 |
| 全量 `server/tests/ -x` 入口 | 94 通过、1 失败，后续未运行 | `full-entry.xml`；既有 `test_started_worker_crash_after_model_request_is_never_restarted`：Worker 断开后 `stdin.drain()` 的 `ConnectionResetError` 覆盖 `EngineUnavailableError`。与上次同位置，相关源码和测试相对 HEAD 无 Diff，本批未修。 |
| Diff/敏感信息检查 | 通过限定检查 | `git diff --check`；五个目标文件未命中常见 OpenRouter/GitHub key 或私钥模式。此检查不等同完整安全认证。 |
| 范围与历史证据 | 已核对 | 106 个冻结源文件中仅三个约定生产文件发生变化；新增一份测试、一份审计。原 127 个状态项增至 129，无暂存内容；八份历史调用账本 hash 均未改变。 |

测试 runner：`C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-closeout/run_offline_v2.py`。它禁用 dotenv 凭据加载、外部网络，重定向 Runtime Store；新增测试进一步禁止 Provider 与业务记录 API。核心命令：

```powershell
# 重点范围，报告写入本次证据目录
& C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-closeout/run_offline_v2.py server/tests/test_meta_planner_control_dependency_repair.py server/tests/test_meta_planner_repair_context.py server/tests/test_meta_planner_control_flow.py server/tests/test_meta_planner_control_domains.py server/tests/test_meta_planner_path_proof.py --junitxml=.tmp-cw10-dependency-repair-20260919/focused-closeout.xml
# 全量入口：明确在首错停止，不宣称其余通过
& C:\tmp\modelmirror-mcp-test-venv\Scripts\python.exe -B .tmp-cw10-closeout/run_offline_v2.py server/tests/ -x --junitxml=.tmp-cw10-dependency-repair-20260919/full-entry.xml
# client 目录下构建；不覆盖预览器 dist
npm.cmd run build -- --outDir ../.tmp-cw10-dependency-repair-20260919/client-dist
```

### 能证明与不能证明

- 能证明：原有安全门禁未被绕过；端口错误与路径错误共存时，唯一修复 Prompt 仍得到权威反例；只修端口、重提配置、猜测控制边均不能通过。
- 能证明：保持条件和写入配置不变时，公共生产者与分支内生产者两种显式 Patch 均可通过编译/反编译往返。另覆盖多路由、ref 改名、null/读取 error 路径及非祖先并行依赖，不局限于单条线性链。
- 能证明：离线脚本化 completion 仍最多三次；不完整修复停止，Proposal 保持 pending r1，不创建 Xpert。脚本响应仅证明工程链路，不是 Provider 生成能力证据。
- 不能证明：真实模型一定能正确利用反例、真实生成稳定性或泛化成功率。未开展新一轮真实调用、活表写入、浏览器验收或预览器重启。
- 当前结论：本批离线修复完成；真实复测及全量门禁尚未完成，不提交 PR，不宣称 CW10 已稳定。
