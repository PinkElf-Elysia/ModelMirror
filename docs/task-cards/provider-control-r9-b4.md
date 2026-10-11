# R9B4：Readiness 与运行准入一致性

## 范围与基线

- 基线：`origin/main@e5a808a60b024635a1e13965558f15775c5af86a`；已 Fetch 并核实 B3 PR #409 合并。
- 当前集成基线：2026-10-11 经授权接入 `a9ac1f13c160dab6643952d43e78ea95c2006bb4`；下列 `acceptance-02` 结果仍仅属于原基线，不继承为新基线通过或豁免。
- 工作树：`C:\tmp\modelmirror-control-r9-b4`；分支 `codex/provider-control-r9-b4`，开工状态干净。
- 单一目标：公开 Readiness、控制面 Catalog 与真实 Managed 准入复用判定；目录在线不等于已获准调用。
- 不改模型市场/提示词数量、默认 Provider、R5 500/14 门禁、协议、生产开关或数据库 Schema；不调用真实模型。
- 不触碰主工作区、B3、其他预览和持久化数据；Commit/Push/PR/部署另行授权。

## 已核实问题

| 事实 | 代码证据 |
| --- | --- |
| Managed Offering 仅凭连接在线、目录未 stale 就标为 invocable | `server/model_router/control_plane_catalog.py::_managed_evidence` |
| Chat public_status 不检查 required Gate，且检查全部备用路由；运行只允许 required 首目标 | `chat_control.py::public_status`、`chat_stable.py::_begin` |
| Chat readiness 调用会同步 Gate 纪元的 get_policy | `chat_stable.py::_readiness`、`chat_control.py::get_policy` |
| Workload 公开判定和 prepare_call 各维护一套检查 | `workload_control.py::public_status/prepare_call` |

## 实施小批次（每批最多五个文件）

1. 本卡、Chat 判定/只读拆分、Stable 复用、针对性回归。
2. Workload 共享判定与逐入口真值表；保留运行/逻辑键/Egress 最终复核。
3. Catalog 复用资格与入口准入，保留目录存在性、legacy 能力及数量口径的边界；修正相关测试。
4. 受影响帮助内容、独立无付费预览及最终门禁（部署先授权）。

允许路径限定 `server/model_router/`、对应 `server/tests/`、本任务卡、控制面文档及必要帮助内容。无新生产依赖、无数据迁移。

## 验收与风险

- 同一模型/形态下：无认证、到期、停用、配置/凭据/Adapter 漂移、不同入口 Policy、required Gate 降级与备用目标不得产生假阳性。
- Readiness/Catalog 重复读取：无 DNS、HTTP、认证、刷新、Receipt、纪元或数据库写入。
- 静态准入不宣称当前网络可达；真正发送仍执行 Egress 授权、单 IP、原子派发与策略复核。
- 对照真实运行准备路径；不以单元 Mock 代替真实 Provider 或人工验收。
- 风险：把 legacy 能力、目录声明与入口级 Managed 可执行混为一谈；只读拆分漏掉硬失败判断；错误改变 required/备用语义。
- 立即停止：重复 POST、未授权回退、内容/凭据泄露、数量口径变化或无法归因的门禁失败。

## 验证记录

| 检查 | 命令/范围 | 状态 |
| --- | --- | --- |
| Chat 小批次 | 新增真值表 + `test_provider_chat_control_service.py` + `test_provider_chat_stable_service.py`；63 passed、1 skipped | 通过 |
| Workload 小批次 | 新增真值表 + `test_provider_workload_control.py`；早期 67 passed，最终已纳入下述专项和全量 | 通过 |
| Catalog 小批次 | `test_control_plane_catalog_service.py` + `test_control_plane_catalog_api.py`；9 passed | 通过 |
| 最终专项 | Readiness、Chat control/stable、Workload、Catalog 服务/API；135 passed、1 skipped | 通过 |
| 最终全量 | `acceptance-02`：Linux 后端 8921 passed、30 skipped；独立 Workflow 7 passed；前端 1208 passed、3 项获批基线失败 | 旧基线按批准豁免完成，不是全绿 |
| 其他最终阶段 | Guard 43 passed；Worker、Catalog、typecheck、production build、help assets 通过；补充 Node 契约 17 passed | 通过 |
| 预览与帮助重放 | 独立 15164 合成预览、目录/资格/路由与帮助文章重放；零模型请求 | 通过，详见帮助证据 |
| 配置与差异审查 | Core/newAPI/B4 预览 Compose `config --quiet`；差异空白、已知凭据签名、最终范围审查 | 旧基线通过；新基线结果见末节 |

不自动沿用 B3 基线豁免。发现失败时在本基线重新复现并单独处理。

### 开发回归与契约说明

- Chat 三项新增反例先红：Readiness 同步纪元、required Gate 被忽略、required 使用备用资格；修复后均通过。
- Catalog 三项新增反例先红：在线目录未配置路由、认证后未激活路由、资格过期仍可调用；修复后均通过。
- 现有 scoped Chat 断言更新为与公开状态一致的 `provider_chat_model_not_stable`；仍断言不能进入普通稳定 Chat，未放宽作用域。
- Workload 公开 `blocks_before_dispatch` 按网关接管条件修正：部署开关关闭或策略 legacy 不拦截旧路径；Managed `available=false` 不伪装成 legacy 也不可用。
- Catalog 加法返回 `managed_readiness`（入口、形态、可用性、原因），不返回连接/认证/内部策略；operation 的任一可用入口不代表其他入口已获批。Managed access mode 明确为 `managed`，不再误写成 `native`。
- 首次 Windows 测试临时目录 ACL 拒绝；改用全新 `C:\tmp\r9b4-pytest-<UUID>`，没有清理或复用其他测试目录。
- 组合回归：Chat/Workload/Catalog 服务及 API 163 passed、1 skipped；这是开发阶段快照，仍需最终源码全量验证。
- 证伪发现：required 策略的工具资格行被删除时，只读文本查询原先仍可通过，而运行同步会降级。新增反例先红，修复为所需资格集合与保存集合精确相等，公开与运行共用完整性判断。
- 最新窄回归：覆盖静态检查、Catalog、Readiness 真值表及 Chat control，66 passed（52.52s）。过期合成夹具同时设置合法的历史开始/结束时间，避免把“时间字段矛盾”误测为“正常过期”。

### 同基线归因与本批批准的豁免

2026-10-11 再次 Fetch，`origin/main` 仍为 `e5a808a60b024635a1e13965558f15775c5af86a`。没有沿用 B3 豁免。

- 首个冻结快照 `modelmirror-r9b4-acceptance-01`：源码摘要 `fd14b6bdab588c4bf080e36bc84acccb2e4c5984f3029c031acf33138212736c`，镜像 `sha256:116bce80b747d62969109e1319d7274dcabbfeb8984b7090c79503441753b8fb`。Guard 43 passed、独立 Workflow 7 passed、Worker/Catalog/typecheck 通过；Linux 后端 8920 passed、30 skipped、1 failed。该快照早于帮助增量和下述断言修正，不作为最终版本全量通过证据。
- 唯一 Linux 后端失败是 R8C 旧测试将显式停用策略也断言为阻断。B4 契约要求停用恢复 legacy，漂移保持 degraded_required。先复现 1 failed、1 passed，再修正测试，同时核对网关模式、公开模式、阻断原因及零 Call；回测 2 passed。没有为通过测试放宽生产准入。
- 前端全量 1206 passed、3 failed；同 SHA 干净归档、同镜像依赖与时钟下，两个对应测试文件复现相同 3 项失败（78 passed）：目录 live 数量预期 585、实际 569；到期模型列表；专家团固定模型选择。用户明确批准仅这三项为 B4 非阻断基线豁免，另案处理，不涵盖准入、安全或新增失败。
- Windows 受影响回归原有 1113 passed、7 failed、2 skipped、34 subtests。其中 2 项音频元数据 100ms/500ms 时限失败在同 SHA 干净工作树、同 Python 环境复现；同依赖 Linux 基线这两项通过。用户明确批准仅这两项 Windows 非阻断豁免；最终 B4 Linux 全量仍必须通过。
- 其余 4 项 RPG 编译器失败来自隔离工作树缺少已锁定的 `ajv` 依赖。在 B4 与干净基线各执行既有锁文件的 `npm ci --ignore-scripts --no-audit --no-fund` 后，两边均 4 passed；没有新增依赖或改动锁文件，不列入豁免。
- 干净基线工作树：`C:/tmp/modelmirror-r9b4-baseline-20261011`。Linux 基线归档 SHA-256：`fe4c80ac2b719bb8f0981eddbf3a192f20db6a33a54e2c130683979e709758df`。归因容器 `r9b4-baseline-frontend-20261011` 与 `r9b4-baseline-backend-20261011` 均无网络、无持久卷；后者只运行 8 项针对性测试并全部通过，不冒充基线后端全量。

最终快照 `modelmirror-r9b4-acceptance-02` 已包含帮助资产和 R8C 测试修正，Linux 全量与前端各阶段均已结束。前端全量及 `provider_acceptance.py verify` 均保留退出码 1，仅对应三项已批准基线失败；没有修改 harness 或跳过断言。详细身份、报告哈希、命令和边界见 [B4 验收归档](../audits/provider-control-r9-b4-closeout.md)。

### 归档审查与主线更新

2026-10-11 归档时 Fetch 成功，主线从 `e5a808a6` 更新至 `a9ac1f13c160dab6643952d43e78ea95c2006bb4`（PR #410）。直接交叉文件为 `client/src/content/help-center/index.ts` 与 `helpContent.test.ts`；间接影响包括模型目录、目录测试、多模态就绪清单及 Decisions 白名单。没有自动 rebase、改目录口径或继承旧豁免。

原基线归档结论：`e5a808a6` 上的 B4 实现及合成预览证据可归档；归档本身不证明最新主线通过。

用户随后授权“开始下一步”。已完成无功能提交的基线 rebase，并恢复 B4 改动。恢复前完整快照为 `C:/tmp/modelmirror-r9b4-pre-integration-20261011`，内容摘要 `b6b2a236cbee2f20130f1201895acb6f4a47a2ce61a23fe9f590d48e9c6b81d9`；专用 stash `a9d98745aa66a25ddc4e71323bb8cb73ef39abf1` 保留，不删除其他 stash。唯一内容冲突为帮助测试的独立基线文章名单，已保留双方条目和断言解决；自动合并后的目录也保留双方文章。恢复后逐文件核对，除这两个交叉文件外，原 20 个 B4 文件内容未变。

新基线正在重新冻结与验收。宿主前端第一次启动因本地依赖缺失未执行测试，随后按现有锁文件安装依赖，不列为产品失败或豁免。旧基线三项前端及两项 Windows 豁免不自动继承；如新基线失败，必须重新归因。没有 Commit、Push、创建 PR、生产启用或真实模型调用。

## 回退与完成边界

新基线最终结果：`acceptance-03` 后端 8925 passed、30 skipped、0 failed，独立 Workflow 7 passed；窄后端 158 passed、1 skipped，前端全量 1214 passed、1 项专家团固定模型失败。该失败已在同 SHA、同镜像依赖的干净归档复现，用户批准仅这一项新基线非阻断豁免，另案处理；两项旧目录失败已通过。Guard、Worker、Catalog、typecheck、build、help assets 通过；独立补跑 Node 合同 17 passed。验收包完整性核验通过，整体退出码因获批前端失败仍为 1。

用户已授权且完成 `15165` 独立无付费合成预览重放，保留旧预览和数据。最终核对冻结清单 5138 文件，仅三份归档文档漂移；19 个文本文件凭据签名扫描零命中，Diff 检查通过。2026-10-11 用户明确批准原截图证据复用及收尾后提交 PR；保留截图旧基线标记，不宣称新截图已落盘。发布前 Fetch 确认 `origin/main` 仍为 `a9ac1f13`，验收包复核结果不变。详细身份与边界见验收归档的新基线节；提交、推送和 PR 结果以随后 Git/远程记录为准，不预写成功。

仅回退本批代码/帮助内容，无整库恢复或数据删除；运行期安全出口和防重放边界不得撤销。当前发布授权不包含合并、生产启用、下一批实施或真实付费调用。
