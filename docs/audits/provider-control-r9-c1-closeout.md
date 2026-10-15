# R9C1 最终验收记录

## 99e50f99 发布前整合

2026-10-15 用户授权提交 PR，并要求刷新上游、交叉审查及必要回归。Fetch 后基线从 ec79e120 更新到 `99e50f99e341a013c4c826d623d2e75a26483088`。先保留完整未提交恢复快照，再快进及恢复 C1 修改，无合并冲突；尚未 Commit、Push 或创建 PR。

上游 20 个变更路径中，直接交叉为 `server/main.py` 与帮助索引。前者仅增加两个 Decisions 模型枚举；后者注册独立帮助文章，与 C1 可信来源说明并存。间接影响为 STT 的 AssemblyAI WAV 输入验证及 Profile 版本、静态模型目录、价格契约与帮助资产。覆盖复审仍为 81 个分类入口、947 个候选，无新增模型发送位置；只更新上述三个模块指纹与目录静态候选标识，不扩大纳管声明。

新 Jev JPEG 的基线失败已在干净 99e50f99 复现。按本次单独授权保留原图并确定性转为 PNG，更新文章和测试引用；旧 ScaleDown 修复保留。未修改资产校验器。用户另行批准固定依赖核验后的隔离复用及新基线无付费预览；现有 15168 及其他预览不变。

### 新基线提交前验收结果

证据目录为 `C:/tmp/r9c1-acceptance-99e50-offline-20261015`。冻结源码 5408 个文件，内容哈希 `03f4cc7d9e1ccf1ea5c5d7f3286e28941def2773395711f8128aac6a4f46dd63`；验收镜像 `sha256:61438faee8c7f057178e8352e2147ec4879cdc48b30bb6219d21b19c492c5a9c`。11 项依赖输入与获批固定镜像一致，重新装入当前源码执行，不继承旧测试结果。两张 JPEG 的删除仅表示原图移到证据目录，其字节与基线原件一致；独立快照脚本记录这两项精确例外，没有放宽仓库验收脚本。

| 检查 | 当前结果 |
| --- | --- |
| guard / workflow | 通过：47 / 8 项 |
| Worker、Catalog、typecheck、production build | 通过 |
| help_assets | 通过：32 篇文章；含已获批两项 PNG 修复 |
| 前端全量 | 失败：1262 通过、3 失败；仅下列三项获批基线豁免 |
| 附属 Node 检查 | 通过：17 项 |
| 上游 STT、Decisions 与来源适配交叉回归 | 通过：78 项 |
| 当前源码 Shadow / Worker 独立补测 | 通过：36 项、无跳过；真实 Node Worker、合成模型回调 |
| 后端全量 | 通过：10668 项、37 项跳过、0 失败；另有 45 个子测试通过 |
| Compose 配置 | Core、独立 newAPI、新预览实际配置均通过 |
| 新预览合成恢复及再次重启 | 通过；有来源任务完成，缺来源任务阻断，两张模型调用表均为零 |

三项前端断言均在干净 `99e50f99`、同镜像依赖复现，C1 未修改对应目录数据或断言。用户分别明确批准本批非阻断并另案处理，仅限：

- `models.oct10.test.ts:14`：Step 5 上下文长度预期 1000000，基线为 1048576。
- `models.oct10.test.ts:19`：Ling Sante 预期 `live`，基线为 `uncertain`。
- `models.oct12.test.ts:14`：旧别名 `~perplexity/pplx-decider-27b-latest` 预期 `live`，基线为 `uncertain`。

原始 `frontend_tests.json` 仍是 failed，完整日志及两个干净基线窄测试日志保留。人工豁免不等于自动全绿，不涵盖来源、安全、后端或其他失败。

独立预览为 `http://127.0.0.1:15169`，镜像 `sha256:079717d88114ee3b775756d0f8069a5cf726649a396b33f463865acfcbcecafa`，全新合成卷、后端 internal 网络及空 Provider 凭据。首次选用的隔离网段与既有网络重叠，Docker 在创建前拒绝；核对后只调整新预览到未占用网段，没有更改旧网络。启动恢复结果为有来源任务 completed、previous_runs=1；缺来源任务 failed、previous_runs=0，错误 `provider_call_context_origin_missing`。再次重启保持相同状态及零调用，原结果保存在 `preview-second-restart.json`。

新预览清空帮助搜索后重放成功，Jev PNG 在浏览器加载完整、尺寸 1000×791。用户提供的本次可信来源说明截图原样归档于 [99e50f99 证据目录](../help-center/evidence/provider-context-r9c1-99e50f99/README.md)。旧预览及其截图没有被覆盖或重新标注。

当前源码另行执行 `python -B scripts/provider_qualification_rollback.py --verify-storage C:/tmp/r9c1-rollback-99e50-20261015`，结果为 `repository_cycle=passed`、`provider_calls=0`。仍仅证明固定 B2 加受控兼容补丁的 Repository 往返，不是未经修改的旧 Server 或完整容器回滚。Shadow JUnit SHA-256 为 `307e61279a9f8d3b165c00d1ff5a1b16aa68f029d578f6c967671e4cf7feba8b`。

后端原进程于 2026-10-15 09:57 UTC 正常退出 0，无 OOM；pytest 耗时 2777.42 秒。`backend.xml` SHA-256 为 `3d6adb7ced4a4df95b8120709c0dd521820cec1c9291ff77b69d3c42cc299aaf`，与阶段报告一致。全量阶段命令为 `python -B -m pytest server/tests/ --ignore=server/tests/test_workflow_run_contract.py -q -p no:cacheprovider --junitxml=/evidence/result.xml`；被拆出的 Workflow 合同在独立阶段实际执行并通过 8 项，不是豁免。

本次 JUnit 的 37 项跳过逐项复核后与前次 ID 清单一致：Shadow Worker 1 项、Windows 原生行为 16 项、Coding 专用语言服务器 5 项、Office/MCP 专用环境 15 项。Shadow 项由本次 36 项独立补测覆盖；其余 36 项仍是专用环境未验证，不计为通过，不据此宣称相应环境可用。C1 没有修改这些专用模块实现。

`provider_acceptance.py verify --bundle C:/tmp/r9c1-acceptance-99e50-offline-20261015` 核验全部阶段的源码、镜像、配置及 JUnit 一致性，退出 1 仅因 `frontend_tests` 保留 failed；其余八个阶段均 passed。三项人工豁免不改写原报告。

归档后对冻结清单逐文件复核：5408 个文件中仅三份审计/任务文档发生文字变化，新增两份截图证据文件；运行实现、测试及帮助页面源码未变。105 个原始 Git 路径归并两项 R100 原图移动后为 103 个文件；103 个现存路径的有限秘密模式扫描无命中。暂存仅包含已审查范围，无未合并项，暂存差异检查通过。最近一次 Fetch 的 origin/main 仍为 99e50f99，没有新增交叉变化。

**本次本地提交门禁已收口，包含上述三项获批基线豁免及明确专用环境限制。此记录写于 Commit、Push 和 PR 前，不证明远端 CI、生产或真实 Provider 验收。以下 ec79e120 章节是历史验收，不覆盖本节状态。**

## 结论与范围

2026-10-15，`codex/provider-control-r9-c1` 在 `ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795` 加未提交 C1 修改上完成本地验收与关键路径审查。没有发现新的可确认阻断缺陷；有一项用户明确批准的前端基线豁免、独立帮助资产修复补测及专用环境未验证边界。不能表述为全部测试全绿、远端 CI 通过、生产已启用或完整旧服务回滚已验收。

本记录不授权提交、推送、PR、部署或付费调用。发布前须刷新上游并核对交叉变化；若发生相关变化，按影响范围补审和回测。

## 源码与制品

本机验收目录：`C:/tmp/r9c1-acceptance-ec79-offline-20261015`。原始 JUnit、日志及镜像证明保留于本机，不作为公开管理接口内容，也不随本记录提交。

| 对象 | 指纹或记录 |
| --- | --- |
| 冻结源码 5397 文件 | `fc77f97fe7c53ee66d932cdaaca5040873ddc1f40c2e3033c18407266af5afbd` |
| 验收镜像 | `sha256:efa9e5496a77fb91eb4e76d73e116aa1ea3b792fdd701859385185e7e558e094` |
| 验收 Profile | `dabc7b8a065f40619ef29783f61a7337806bf2530a9fc5b1efeb680944088228` |
| 后端 JUnit | `625935b783c2a3f664a642089562d60b3bb06d0e56bc7058afe962ad4c679c93` |
| Shadow 补测 JUnit | `b230c0c104ef9e7057e1e033e46a6b2fec17536de73c496db49065471d3ae6f0` |
| 15168 帮助增量客户端镜像 | `sha256:7a663a3073ec1178f54fa50a45664bf4e0bab95fbdbff98da02c3ec2b92c15a7` |

构建采用用户批准的 `authorized-dependency-reuse-v1`：11 项依赖输入与固定依赖镜像一致，重新装入当前源码并执行测试，没有继承旧测试结果。最初两次依赖下载失败及一次帮助增量 FROM 引用失败的记录保留。

冻结后逐文件差异仅为 ScaleDown 图片转码与引用、原图保留、帮助截图及审计文档；运行时代码与测试未改变。帮助增量构建只替换 15168 客户端，Server、合成卷和其他预览未重建。

## 验收矩阵

| 检查 | 实际结果 | 证据与处置 |
| --- | --- | --- |
| guard / workflow | 通过：47 / 8 项 | `guard.json/xml`、`workflow.json/xml` |
| 后端全量 | 通过：10659 项；跳过 37 项 | `backend.json/xml`，退出 0，另有 45 个子测试通过 |
| Worker / Catalog / typecheck / production build | 通过 | 同名阶段 JSON；均绑定冻结镜像 |
| 前端全量 | 失败：1261 通过、1 失败 | `models.oct10.test.ts:19`；仅此项获批非阻断豁免 |
| 前端附属 Node 检查 | 通过：17 项 | `node-checks.log`，补齐复合脚本被失败短路的检查 |
| 原 help_assets | 失败 | 原 JPEG 不符合既有 PNG 合同，报告不改写 |
| 帮助修复补测 | 通过 | `help-assets-after.log`；宿主、固定镜像及客户端增量构建检查通过 |
| Shadow / Worker 补测 | 通过：36 项，无跳过 | `shadow-recheck.xml`；真实 Node Worker、合成模型回调 |
| 合成恢复与再次重启 | 通过 | 有来源任务完成；缺来源任务阻断；再次重启不增加运行；模型调用表为零 |
| Compose 配置 | 通过 | Core、独立 newAPI、15168 实际 Overlay；仅 config，不启动共享栈 |
| 差异与有限秘密模式扫描 | 通过 | `git diff --check`；归档前 96 个变更路径无常见密钥/私钥模式命中 |

### 前端豁免与补测

Ling Sante 断言要求 `live`，同 SHA 主线目录将其列为 `uncertain`。干净同 SHA、同镜像依赖的窄测试复现 2 passed / 1 failed；C1 未修改目录数据或该断言。用户只批准该项本批非阻断，另案处理，不沿用其他旧豁免。

一次 client-only 基线探针误跑全量并缺少 RPG 源码，产生额外导入失败；它不作为完整基线证据，也不增加豁免。最终归因使用独立窄测试。

原冻结包执行 `python -B scripts/provider_acceptance.py verify --bundle <验收目录>` 仍退出 1，保留 `frontend_tests` 与 `help_assets` 的原失败。通过结论来自分项结果、明确豁免与后续补测的组合，不修改脚本或原报告制造全绿。

### 37 项跳过

| 原因 | 数量 | 当前处置 |
| --- | ---: | --- |
| 部署版 Node 24 Shadow Worker 不在通用镜像 | 1 | 对应测试已在当前 Shadow 补测实际通过 |
| Windows 原生文件句柄与宿主行为 | 16 | Linux 不适用；本次未完成对应 Windows 全组验证 |
| Coding 专用语言服务器执行镜像 | 5 | 通用验收镜像不具备，专用环境未验证 |
| Office/MCP 专用依赖及 sidecar | 15 | 专用环境未验证 |

测试标识与上一冻结包的跳过集合一致，但本次按新 JUnit 逐项复核，不自动继承历史通过或豁免。后 36 项不计为通过；C1 没有修改这些专用模块实现。

## 审查与回滚边界

审查重点为服务器创建来源、子调用继承、恢复时来源复核、原子派发占位、异步只读恢复、认证来源和公开字段隔离。关键差异未发现新的可确认阻断缺陷。秘密扫描只覆盖明确的常见模式，不替代完整安全审计。

受控兼容 Repository 循环已验证 v18→v20→固定 B2 加兼容补丁读取→v20，并保留合成来源；该结果不是未经修改的旧 Server 或完整容器回滚证明。回退前须显式停用受影响策略、按批准方案关闭开关，处理未决任务并保留幂等、来源、Receipt 和任务记录。不得删除来源以绕过阻断、重放未知请求，或用整库快照覆盖新事实。

## 待发布说明

建议标题：`feature: 持久化 Provider 可信调用来源并校验恢复边界`。

范围：既有 R5—R8 及桥接入口的可信上下文、持久来源、继承和恢复；SQLite v20 加法表；合成回归与帮助说明。附带已获单独授权的 js-yaml 4.3.1→4.3.2 最小补丁和帮助图片格式修复，不新增生产依赖。

覆盖清单当前为 81 个分类入口、947 个静态候选；ScaleDown 明确为 `migration_pending`。C1 不宣称 R9D 全覆盖，不执行账户、计费或默认 Provider 切换。

### Help Center Impact

- 受影响任务：恢复时遇到缺少可信来源，识别阻断含义并保留旧运行供管理员核查。
- 文章：`client/src/content/help-center/articles/recover-unavailable-feature.md`；同步帮助索引。另有 ScaleDown 文章的图片引用修复。
- 预览：固定 ec79e120 加 C1，独立合成 15168；帮助搜索、处置指引及图片加载已重放，无付费调用。
- 截图：[原图与来源说明](../help-center/evidence/provider-context-r9c1-ec79e120/README.md)。15167 旧截图不作新基线证据。
- 未验证边界：真实 Provider、生产配置、上述专用环境及完整旧 Server 回滚。

完整实施历史见 [任务卡](../task-cards/provider-control-r9-c1.md)，入口与恢复矩阵见 [来源复审](provider-control-r9-c1-context-review.md)。正式发布说明须在获准刷新上游后再次核对，不能直接将本草稿作为远端 CI 结论。
