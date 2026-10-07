# CW10 Recovery E 集成与验收记录

2026-09-27 最新验收增补见
[固定候选隔离效果验收](META_PLANNER_CW10_ACCEPTANCE_20260927.md)。以下为历史冻结记录，
不覆盖增补中的最新主线、真实调用、部分通过和未完成门禁。

日期：2026-09-20。状态：E1 集成完成；E2 离线全量已执行但门禁失败；E3 真实泛化验收未开始。
本文不把 A-D 的离线证据、旧实例成功或模拟 Provider 结果作为本批真实调用结论。
2026-09-22 恢复后的 Linux 环境核验见
[独立核验记录](META_PLANNER_RECOVERY_E_LINUX_VERIFICATION.md)。下文 Windows 原始结果保留，
不被局部复跑或更换环境后的结果覆盖；测试仍固定于 `09e8a7d6`，不是最新远端的通过证明。

## 固定身份与范围

- A-D 源分支：`codex/meta-planner-controlled-writes-10-closeout@a7d99925`。
- 冻结 166 个受控修改/未跟踪路径：59 个已跟踪修改和 107 个新增文件。
- E 分支：`codex/meta-planner-cw10-recovery-e`，基线
  `09e8a7d644f626b3e1aa606fcf148f360fc9aadc`。
- 独立对照：同一基线的 detached worktree，不包含 CW10 修改。
- 上游 26 个提交、385 个路径与冻结范围仅在 `server/main.py` 和
  `client/server.mjs` 相交。集成转移时其余 164 个路径与冻结内容逐字节一致。
- `main.py` 机械应用后保留上游 Provider 控制面；`server.mjs` 保留新增的 `upstream`
  参数及既有代理分派，同时保留本轮仅作用于生成接口的超时代理。
- 未修改原工作树、旧预览器、共享栈或真实业务数据。没有 Commit、Push、PR、审批或发布。
- 收尾复核：源 166 个冻结文件 SHA-256 无漂移；E 中有 168 个变更路径、暂存为空。
  原冻结路径中的额外差异仅为两处上游相交、代理测试补证与批次记录；新增审计文档及
  原本未改的 Managed 端点测试单列。没有新生产依赖或 Runtime 迁移。

冻结 manifest、补丁和逐命令日志位于各自工作树的忽略目录 `.tmp-recovery-e/` 或
源工作树 `.tmp-cw10-recovery-e/frozen/`。这些私有诊断材料不是待提交内容。

## 验证口径

离线后端使用原 A-D 隔离入口：移除凭据环境变量、禁用 dotenv、将 Store 重定向到新的临时目录、
拒绝非回环网络。模拟 completion 与临时 SQLite 不证明真实 Provider 可用或业务活表写入通过。
不放宽生产校验，不跳过本轮失败，不把上游失败自动视为可豁免。测试夹具的协议迁移及新增
反向断言在下文单列，不将测试修正表述为生产缺陷修复。

| 检查 | 当前证据 | 结论边界 |
| --- | --- | --- |
| `server/tests/` 全量 | 7814 passed / 118 failed / 69 skipped / 4 errors；3110.65 秒 | 8003 个用例；两个用例各有 setup/teardown error，不是全绿 |
| 从同一次全量提取原 65 文件关键矩阵 | 1328 passed / 0 failed / 0 skipped | 是完整运行的子集，不重复计数，不替代全量门禁 |
| 前端首次全量 | 1071 passed / 2 failed；另有两个 suite 导入失败 | 不是全绿 |
| 两个 RPG suite 依赖准备后复跑 | 18 passed | 仅补齐其锁文件已有依赖，无源码或锁文件变更 |
| 前端完整依赖后全量 | 1089 passed / 2 failed；148 passed / 2 failed files，494.66 秒 | 两项失败均已在纯上游复现；不是全绿 |
| 前端生产构建 | 通过；保留既有超过 4500 kB 包警告 | 不等同真实浏览器验收 |
| `node --test server-headers.node.mjs` | 11 passed | 包括真实本地代理路由，不调用 Provider |
| Managed 入口、Recipe 集成、显式修复 API 三文件 | 21 passed / 4 既有 FastAPI warnings，41.03 秒 | 模拟 Provider，不证明真实调用 |
| 改动 Python AST 解析 | 111 文件通过 | 不执行应用 |
| 已知凭据模式和产物路径检查 | 168 路径，0 命中；暂存为空 | 不声称发现任意形式的秘密 |
| 帮助图片检查 | 3 个未引用旧 JPG，纯上游结果相同 | 未删除无关资产，仍是帮助门禁失败 |

前两次后端全量尝试均已中断，不计完整回归：第一次暴露 Agency Worker 未构建；
补齐锁定依赖并构建后，原健康检查从失败变为通过。第二次终端缓冲无法定位当前用例，
改用逐例日志及 `faulthandler_timeout=120` 的完整运行；超时仅输出堆栈，不更改用例判定。

### E2 单目标修正：Managed 测试夹具的生成协议

- 原端点用例的第三次模拟响应仍返回完整 GraphIntent，C 的正式入口已经严格要求 Recipe。
  同一用例在纯上游通过、E 独立复现失败；失败原因不是生产 Managed 网关不可用。
- 仅修改 `test_meta_agent_managed_endpoints.py`：使用既有 `from_intent` 测试转换器生成
  Recipe，并验证生成和修复请求均声明协议标记、`control_flow` 及未知字段拒绝规则。
  不改 Service、Prompt、Adapter、网关、调用预算或任何生产验证器。
- 新增两条降级攻击：完整旧图不带标记，以及夹带伪造 V1 标记。两者均被拒绝，后者明确
  报告 `control_edges` 和 `nodes.0.outputs` 非法；三次调用回执保留，不回退 legacy 网关。
- 攻击测试初稿错误地期待 API 未暴露的 Pydantic 内部错误码；实际生产入口已经拒绝。
  改为断言现有 API 的 issue code 和字段诊断后，重跑同样三文件为 21 passed。
- Sol 在独立基线工作树只读复核最终 Diff 与 JUnit：此前提出的伪造标记覆盖缺口已关闭，
  未发现与此测试修正直接相关的新 P1/P2；该复核不是独立运行或真实 Provider 证据。
- 原 Windows 全量在此修正前已收集 8003 项并载入旧夹具；其中该失败必须与上述定向复跑分别报告，
  不把局部通过改写成最终 8005 项全量通过。随后 Linux 按 CI 的 contract/remaining 拆分重新运行，
  使用修正后夹具；完整结果与隔离边界在独立核验记录中单列。

另在已有代理测试中补充上游相交路径：普通 health、显式修复 preflight 仍走普通 API 代理，
`/rpg-app/health` 走独立 RPG 上游；仅生成 POST 获得 Planner 关联标记。最终 11 项通过。

## 独立基线对照

这些问题在同一纯上游基线上复现，不是凭文件未改而推断。仍属于总门禁的未解决项，
本批没有修改无关生产实现、测试断言或模型目录。

| 问题 | 独立复现 | 范围 |
| --- | --- | --- |
| Worker 已派发后崩溃，清理阶段覆盖预期异常 | 1 个 E 失败用例同败；基线文件 3 passed / 1 failed / 1 skipped | Windows 下 `_run_once` 清理的 stdin drain 抛出 `ConnectionResetError`；未知派发不得重启 |
| Coding Backend | 66 个 E 失败用例均在纯基线复现 | `fchmod`、Unix socket、symlink 权限、Git 换行/快照以及 Worker 超时/清理问题；不统一归因成一个产品缺陷 |
| 文件解析、Office sidecar 与 MCP | 19 个 E 失败用例均在纯基线复现 | 包含 CRLF/编码、POSIX 路径与 API、子进程 deadline；部分只确认同败，未宣称每项完整根因已查明 |
| 多模态目录与 R8c | 4 个 E 失败用例均在纯基线复现 | 目录版本/格式断言漂移及模拟超时恢复；不涉及真实模型调用 |
| Skill | 15 个 E 失败用例均在纯基线复现 | 路径顺序、Node exit 134、生命周期冲突、symlink 权限、fsync 坏描述符及固定信任回执字节不符 |
| RSS | 1 个 E 失败用例同败 | 测试用默认 GBK 读取 UTF-8 文件失败 |
| Plugin Hooks | 11 个 E 失败用例均在纯基线复现 | 固定脚本验证及执行边界失败，主要为 `skill_hook_contract_stale`，不放松信任校验 |
| 巨型参数测试环境错误 | 2 个 E 用例均在纯基线复现，共 4 个对应 errors | `PYTEST_CURRENT_TEST` 超过 Windows 32767 字符；基线小组另有 1 个 teardown 串扰 error，不计为独立产品问题 |
| ModelCard 分时价格文案断言 | 9 passed / 1 failed | 缺少测试预期的当前价格窗口展示 |
| tokenPricing 当前 UTC 时段断言 | 3 passed / 1 failed | 固定测试时点期待窗口但得到 null；不在本轮改目录以迎合断言 |

后端 120 个唯一失败/错误用例中，119 个在纯上游复现，即 117 个 failed 用例及上述两个错误用例。
唯一 E 独有的旧 Managed 夹具差异已按单目标修正，并通过最终 21 项定向复跑。
此结论只说明本次离线证据未发现其他 E 独有失败，不证明所有上游问题均无影响或可自动豁免。

三个 Coding 用例首次基线通过、再次原环境运行失败，E 单独复跑也存在变化；保留该不稳定性，
不把它们写成稳定、单因果结论。另两个仅限子进程的 Git 配置对照显示系统 `core.autocrlf=true`
影响第一层错误；禁用该配置后仍在后续 Windows 编码或 `fchmod` 失败，不构成通过证据。
未修改用户 Git 配置。独立基线最终 Git 状态干净、暂存为空，测试进程已结束。

帮助图片检查在 E 和纯基线都失败：`b266b870/studio-beta-headings.jpg`、
`eeb5bbd2/creator-cache.jpg`、`eeb5bbd2/studio-layout.jpg` 未被引用。本轮不删除无关资产。

### 可重放证据

- E 原始后端：`.tmp-recovery-e/backend-full-verbose.xml`，SHA-256
  `a6d328cfffc035f5a21c00527ec94eb7c854002188773fc38b3a83861cc90ced`。
- 受限逐例对照：`.tmp-recovery-e/baseline-comparison.json`，包含文件、受限测试名、完整
  node ID 的 hash 与对应基线报告，不嵌入巨型参数正文。
- 修正后定向：`.tmp-recovery-e/managed-endpoints-final.xml`，SHA-256
  `d11362962b0cd3ce25a0e6037c89ecc37a866e99bae6fedb71fd4c0b4de9dd04`。
- 前端：`frontend-full-ready.log`、`frontend-build.log`、`proxy-integration-final.log`。
- 独立基线：`worker-baseline.xml`、`coding-exact-baseline.xml`、`coding-extra-baseline.xml`、
  `coding-three-baseline-rerun.xml`、`pre-meta-baseline.xml`、`multimodal-baseline.xml`、
  `skill-baseline.xml`、`skill-extra-baseline.xml`、`late-baseline.xml`。

实际命令（后端在工作树根目录，前端在 `client`）：

```powershell
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-recovery-e/run_offline.py server/tests/ -v -v -o faulthandler_timeout=120 --junitxml=.tmp-recovery-e/backend-full-verbose.xml
& C:/tmp/modelmirror-mcp-test-venv/Scripts/python.exe -u -B .tmp-recovery-e/run_offline.py server/tests/test_meta_agent_managed_endpoints.py server/tests/test_meta_planner_recipe_integration.py server/tests/test_meta_planner_model_repair_api.py -v -v --junitxml=.tmp-recovery-e/managed-endpoints-final.xml
npm.cmd run test:run
node --test server-headers.node.mjs
npm.cmd run build
npm.cmd run verify:help-images
git diff --check
```

上述 Windows 后端全量使用变更前已载入的 Managed 测试模块；当时修正后的 8005 项全量尚未运行。
后来 Linux 首次完整运行已覆盖修正后夹具，7957 passed / 9 failed / 32 skipped，加上独立 contract
7 passed 共 8005 项；不能称为全绿。生产实现未因该夹具修改而变化，也未将任何局部复跑替代原失败结果。

### Linux 核验与上游新鲜度

- 最终成对选择集均为 126 项、123 passed / 3 failed，与纯上游相同；脚本、归档、选择集、解包
  源码树和容器隔离投影已固定。没有将被选失败在 Linux 消失简单归因于一个 Windows 缺陷。
- 首次全量额外四项 RPG 失败为缺少 ajv，在纯上游复现；两项 OpenBLAS 线程创建失败在 E/基线
  独立运行都通过。补齐锁定依赖和固定 BLAS 单线程后，纯上游六项通过；E 最终 remaining 为
  7963 passed / 3 failed / 32 skipped，7998 项，1276.186 秒。加上独立 contract 共 8005 项，
  7970 passed / 3 failed / 32 skipped，无 errors。原 65 文件关键矩阵仍为 1328 passed。
- 最终只剩三项纯上游音频失败，原六项环境失败均通过；没有将 126 项成对小矩阵重复加入总数。
  两次 full 的五个 XLSX 参数 ID 因动态 ZIP 时间戳改变，对应函数和样例数量不变且均通过，
  不声称所有参数正文完全一致。最终 JUnit hash 与源码/环境绑定详见独立记录。
- 音频剩余项涉及旧目录版本、fixture 和 Qwen 测试范围；合成取证另复现 Muse WAV-only 的目录
  格式展示扩张。前端两个价格测试与三个帮助资产问题仍未修复，均不在 CW10 夹带补丁。
- 2026-09-22 本地 `origin/main` 引用已前进到 `23e6c165`，新增 9 个提交、80 个路径，与冻结
  168 路径无直接交集。本次不合入新提交，也不宣称无文件交集就没有传递影响；提交前仍需刷新、
  审查并重新验证实际提交基线。
- 本次仅补充三份审计/任务文档，未改生产代码或测试断言。命令均已结束，本任务临时容器已退出，
  `git diff --check` 通过；未进入 E3、未调用 Provider、未提交或创建 PR。

## E3 固定实测矩阵

以下为待授权验收输入，不是已发生的调用，也不扩大真实表权限。使用新的零记录合成表，
初始化数据仅放入私有 Evaluation Backend；不复制业务记录，不写活表。
表 ID、Schema checksum、精确模型及外发正文在实测前固定并交用户确认。

| 编号 | 固定目标 | 核验场景 | 防止替代证据 |
| --- | --- | --- | --- |
| E-G1 | 库存检查：查 `DEMO-A`；库存低于 5 时只把该条状态改为待补货，否则不改；查不到明确停止；最后中文说明 | 无记录、库存 2、库存 8；旁路哨兵不变 | 真实互斥路径，不能改成无条件串行更新 |
| E-G2 | 申请处理：查 `REQ-E02`；待审核改为待复核，已撤回则删除该条，已完成不动；其他状态或缺失明确停止 | 缺失、待审核、已撤回、已完成、未知状态；最多一行 | 多路业务结果和默认路径必须保留，不能减少目标换取通过 |
| E-R1 | 对可安全保留的失败 Intent 进行人工修复 | 错误定位、改动、无副作用预览、一次 revision 应用、第二窗口冲突 | 标记为人工修复，不计模型初次生成成功 |
| E-R2 | 对同类失败 Intent 显式申请一次定向模型修复 | 精确正文确认、独立调用回执、手工载入、独立预览与应用 | 仅在另行授权后调用；建议不自动保存，不隐含额外修复次数 |

E-G1 只请求该合成表的 `update/status/1`；E-G2 只请求对应表的 `update/status/1` 和
`delete/1`。查询授权独立。业务字段采用 `sku/stock/status` 与 `request_code/status`，
分别使用整数库存和字符串状态，避免仅重命名同一原质检实例。

真实生成前只向模型发送目标、零记录表安全元数据和受限契约；不发送夹具记录、活表数据、凭据或路径。
实际隔离评测是后续单独授权步骤：先展示生成候选、需发送的合成记录和下游 Agent 调用上界，
再执行每个分支并核对 Backend 效果和语义路径。没有视觉调用。

初始生成仍每个目标最多三次 completion；不得自动串起多个目标消耗预算。
若失败，固定保存原始结果、尝试编号和原因，先报告，不临时改写目标或追加调用。
E-R2 不计入初始三次预算，必须再次显式批准一次 completion；结果不确定时不自动重发。
一次或两次成功只能证明该固定矩阵通过，不能宣称统计意义上的普遍稳定性。

## 交付与回退

- 本次停在 E2：全量及帮助门禁失败，尚不具备 PR 提交资格；E3 的正式预览器、帮助教程重放、
  真实初次生成、显式模型修复和隔离效果验收全部未运行。没有用 A-D mock 页面填补这些证据。
- 本地 Linux 核验已按用户授权推进，但不等同 GitHub Ubuntu/完整 CI；剩余基线与 CI 依赖准备
  问题交相应模块独立处理，不在 CW10 增加无关补丁，不擅自降低门禁。
- E1/E2 通过后才进入正式独立预览器验收；旧 D 前端 mock 页面不能作为 E 后端证据。
- 完整帮助教程须在清空本地未应用状态后重放，未授权的付费和写入步骤明确标为未验证。
- 真实调用、业务写入、Proposal 批准、发布和提交权限分别判断，不沿用历史额度授权。
- 总门禁不通过时保留 E 工作树和证据，停止进入下一轮，不自动创建 PR。
- 回退仅停止使用 E 集成副本，保留 A-D 冻结产出与原环境，不撤销任何历史业务写入。
