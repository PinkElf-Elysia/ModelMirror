# R4a 交付与离线验收

> 历史阶段记录：下文的零真实调用、待验和基线描述对应当时阶段，原始结果保留。后续已完成并获用户接受的 8 次真实验收见 [真实验收摘要](CHARACTER_R4A_REAL_ACCEPTANCE.md)；最新主线组合、最终范围及剩余门禁以 [收口报告](CHARACTER_R4A_CLOSEOUT.md) 为准。本次收口没有追加模型调用。

日期：2026-11-14。状态：实现与离线验收完成，等待用户页面验收；未进入 R4b。真实产品模型调用 0，未提交、推送、部署或修改共享服务。

## 基线与范围

工作树 `C:/tmp/mm-cr4a`，分支 `codex/rpg-character-r4a`，基线 `8ed5ed55f5d480968f7a2060b0036594690cafa4`。与计划中的 `b84a17d1` 相比只增加独立 E6 实验的 29 文件；未吸收其他工作树未提交内容。

新增 `character/long-context/` 的独立装配、配置、预算、存储、API、受控注册和页面模块。父前端仅新增帮助文章、精确登记/测试和 2 张已核验中性截图。旧 character/authoring 的 298 个既有文件 SHA-256 全部未变，包括旧合同和兼容清单。

## 实现结果

| 要求 | 结果与证据 |
|---|---|
| 旧单聊、群聊、R3b 新续聊分支 | 通过。可信宿主读取，保留身份/版本/参与边界/有效历史；R3b 来源可重开同一 R4 分支 |
| 按容量和显式窗口 | 通过。单聊整回合，群聊整消息；开场默认固定、可取消固定；无开场不补造 |
| 来源与裁剪解释 | 通过。可见性先过滤，再裁示例、较早历史、角色书；必需内容不足时拒绝 |
| 最终模型先于装配 | 通过。参数解析后确定模型与输出预留；预览与发送使用同一个计划 |
| 精确重生成 | 通过。保留原上下文与参数，不用新窗口重装；不支持条件明确拒绝 |
| 本次参数与默认值 | 通过。分支/成员默认显式保存，派生时快照；能力漂移、未知字段、过期预览和 CAS 冲突拒绝 |
| 原入口共享预算 | 通过。原单聊、原群聊、R3b、R4a 四入口 mock 共用任务硬上限；配置登记不增加额度 |
| 旧预算与未知结果 | 通过。独占只读旧账本租约，旧消耗和预留计入；未知只查原请求，不重放 |
| 记录、回执、恢复 | 通过。准备/结算前后故障注入；重复回执不重复候选。重新计算外层哈希仍不能替换装配计划 |
| 刷新查看上下文 | 通过。只读 `/api/context` 返回已冻结计划与回执用量，未知用量不冒充账单 |
| 页面与导航 | 通过。12 项检查逐项核对；原续写与 R4 使用不同 cookie，保留输入和焦点；长列表面板可关闭 |
| 材料与旧证据 | 通过。全部采用独立中性夹具；原真实验收 108 文件、刷新验收 18 文件哈希全部一致 |

## 自动化检查

所有原始输出位于 `C:/tmp/mm-cr4a-data`，不入库。以下是实际运行结果，不代表 CI 或真实供应商调用已通过。

| 检查 | 结果 | 本地证据 |
|---|---|---|
| R4a 最终 Windows 短路径 | 28/28 通过 | `r4-acceptance.txt` |
| R4a 最终禁网 Linux | 28/28 通过 | `linux-r4-acceptance.txt` |
| 角色与旧合同矩阵 | 209/212；3 项既有失败 | `character-final.txt` |
| 共享宿主 | 144/144，60 项加 13 批共 84 项 | `shared-short.txt`、`shared-batch-*.result.json`、`scope-audit.json` |
| 受控网关 | 187/187 | `gateway-regression.txt` |
| 相关父前端 | 53/56；3 项既有帮助失败 | `frontend-final-3.txt` |
| 聊天前端 | 94/94 | `chat-tests.txt` |
| 当前主线旧存档生成/恢复/续聊 | 4/4 | `latest-main-recovery.txt` |
| 父前端构建 | 通过，有既有 chunk size 警告 | `client-build-delivery.txt` |
| 聊天前端构建 | 通过 | `chat-build.txt` |
| 帮助图片 | 新增两图通过；全仓仍有 24 条既有诊断 | `help-images-final.txt` |

主要命令：

```text
node --test --test-concurrency=1 character/long-context/*.test.mjs
node --test --test-concurrency=1 character/*.test.mjs character/conversation/*.test.mjs character/group/*.test.mjs character/daily/*.test.mjs character/revisions/*.test.mjs character/long-context/*.test.mjs
python -B -m pytest -p no:cacheprovider --basetemp=<本轮独立目录> server/tests/test_rpg_character_contract.py server/tests/test_rpg_group_contract.py server/tests/test_rpg_authoring_route.py server/tests/test_rpg_authoring_execution.py server/tests/test_rpg_parameters_v2.py -q
npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/content/help-center/helpContent.test.ts src/pages/RpgLibrary.test.tsx src/pages/RpgAuthoringEntry.test.tsx src/pages/RpgChatState.test.ts
npm.cmd run build
```

两次 build 分别在 `client/` 与 `experiments/ai-rpg-engine/card-replica/` 执行。Linux 使用已有 `modelmirror-server-node24-test:latest` 镜像、`--network none` 与只读源码挂载，测试数据只写容器 `/tmp`；未拉镜像或部署服务。

当前主线恢复脚本实际 `git archive` 基线是 `8ed5ed55`；部分历史用例显示名称仍包含 `286de1ee`，原日志原样保留，不能据显示名称误判生成源。

## 页面证据与首次失败

最终检查包括：续聊创建、中文多行草稿刷新、默认值与焦点、过期预览阻断、预览/候选/采用、完成后刷新无重复、未保存编辑保护、窄屏键盘与无横向溢出、接话不增玩家消息、刷新后冻结上下文、同浏览器旧/新页隔离、R3b 来源重开同一分支。

前 11 项完整执行记录在 `pages-acceptance-2/failure.json`，该脚本第 12 项提前断言导航完成而失败；随后 `cross-source-pass.json` 定向验证第 12 项通过。该文件名中的 failure 保留原义，不改写为全套一次通过。`panel-close-final.json` 另验证最终面板滚动复位、关闭后草稿及焦点。截图由 Playwright 独立浏览器直接生成；帮助图宽 960/780 像素，均小于 250 KiB，不使用正式材料。

保留的首次失败包括：容量测试夹具取值错误、预算恢复测试使用了错误层级的哈希、测试源预算修改后未重算源哈希、群发送重复使用创建操作 ID、帮助文章初次缺少必需章节/基线登记、帮助截图初始宽度不符、临时脚本工作目录/Windows ESM URL 错误、Playwright 单页快捷上下文不能创建第二页，以及采用/导航等待条件过早。均保留原日志或说明，完成对应修复与复验；未削弱既有断言。

产品修复包括请求事件与独立计划一致性核验、续聊副本请求身份隔离、旧 v1 参数只读投影、R3b 来源准确识别、cookie 隔离、冻结请求计划刷新读取及长面板关闭/滚动复位。没有针对模型叙事问题追加提示词补丁。

## 既有失败、延期与未验证

- `a6563a9b` 三项旧世界失败：`AUTHORING_RUNTIME_BINDING_MISMATCH`、`OFFLINE_SESSION_CORRUPT`、`JOINT_SESSION_CORRUPT`；保留原断言，不扩展兼容哈希。
- 帮助三项既有失败：语音文章基线、旧文章步骤数 11 超过 8、截图路径基线。全仓原有图片格式/尺寸/残留资产共 24 条诊断保留。
- 原始 `git diff --check` 因既有 CRLF 规则返回 2；单独使用 `cr-at-eol` 比较返回 0。没有全局修改换行或关闭检查。
- R3 阶段共享回归的首次失败和中断证据继续保留；本轮沿用短路径分批验证，两条历史首次失败对应测试也已通过。
- 原生文件选择器取消、真机软键盘/触控/系统返回继续延期。供应商真实处理中刷新未验证；本轮只证明离线/mock。
- 未核对当前真实路由、在线价格、真实推理费用与余额，未认证或调用模型。未来真实验收必须另行授权并冻结这些依据。
- 独立预览和代码完成不等于用户人工验收、提交、合并或部署。R4b/R4c、R3c、模板比较、世界、多 Agent/Harness/RAG 均未启动。

## 交付与回退

预览：`http://127.0.0.1:43303/revisions`。角色工作台 `43300`、群聊 `43301`、原续写 `43302`；从 R4 页导航可回到它们。使用目录 `C:/tmp/mm-cr4a-data/preview-acceptance`，仅中性夹具。

接口说明见 `CHARACTER_R4A_HANDOFF.md`。用户完成页面验收且无未解释的隔离/兼容问题后，可讨论 R4b；不自动进入下一轮。回退仅停止本轮预览并撤销独立入口，保留本轮数据，旧数据不迁移、不重写。
