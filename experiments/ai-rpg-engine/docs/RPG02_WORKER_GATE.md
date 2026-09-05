# RPG-02 低级 worker 量产门槛

**量产资格的小批门槛已通过（`qualified_small_batch`），合格型号为 `gpt-5.6-terra`。** 三个全新上下文按同一冻结 Skill 完成两个互斥 job、三个世界及跨上下文续跑，16 条 CLI 全部成功、无重试。用户已授权通过后收尾并提交本轮 PR；实际全量提取放在 RPG-06 第一版路线完成后，另行授权。Luna 尚未取得同等资格。

完整状态、每次尝试、真实 hash 和停止事件见 [机器记录](RPG02_WORKER_GATE.json)。[RPG02_STATUS.json](RPG02_STATUS.json) 继续保留原离线交付的待人工验收状态，并明确 `finalAcceptanceReady=true`、`claimAllowed=false`。技术资格通过与整轮人工验收分别记录。

## 必须通过的门槛

| 条件 | 当前证据 | 状态 |
| --- | --- | --- |
| 冻结工具与 Skill | 固定 assignment、独占页面、静态提取、只新增文件、双输出、事件链及最终回执；worker 不手写解析器或元数据 | 通过 |
| 同一候选型号独立执行 | 三个新 Terra 上下文完成 I/J 两个互斥 job、三个世界；不给正文、预期数量或 hash | 通过 |
| 新上下文续跑 | I1 完成原神并暂停；I2 仅由 assignment/status 读出蛊真人待办，完成并 finalize/audit | 通过 |
| 全部来源和最终回执 | 主审独立复读三世界 DOM；完整 JSON 字段/数组、raw/data hash、零损失、空权限及一致双输出均匹配 | 通过 |
| 失败时停批 | 校验拒绝、路径/覆盖/篡改、链接、排他锁、DOM/字段漂移和实际工具中断均有证据；失败 job 保留 | 通过 |
| 模块回归和隔离 | 原 96 项＋扩展 39 项；固定基线、冻结合同、两目录、研究 MANIFEST 与 diff 检查 | 通过 |

资格按“型号＋冻结配方＋资源类别”授予；不能从单个成功样本推断 Luna、Terra 或其他型号均可量产。

## 实测结果与修正

- 早期 Luna 原神试跑是资源正确、流程有误，仍保留 [原记录](RPG02_LUNA_TRIAL.md)。后续 A/C 任务出现权限流程、占位 payload 和原文转义转录问题，未获资格。最终新配方尚未重新验证 Luna。
- Terra 的 D 任务由两个新上下文完成蛊真人、赛博朋克2077：10 条 CLI 全部 exit 0，文件恢复与最终回执通过。但随后 E 原神被 `WORKER_CAPTURE_INVALID` 拒绝，因此没有据 D 的成功放行。
- 薄弱环节之一是把大段 JSON 再塞进普通 JavaScript 字符串时的手工转义。新增 `encodeWorkerTransfer` 自动转义反引号、美元符及行分隔符，保持 JSON 数据值和来源 hash；配合唯一 `String.raw`＋PowerShell 单引号 here-string 包装。来源内容不作为代码执行。Astra 的真实原神预检 init/ingest/finalize/audit 均 exit 0。
- 新配方 F1：新 Terra 上下文完成原神，4 条 CLI 全部 exit 0，无失败或重试；1 世界、5 身份、30 天赋共 36 项，两份输出均 9767 字节，SHA-256 `e2fce81596cbe3db716099de45b355dd6a66b3ca29f45d931d6bbc7dd89cdfe5`。
- F2：另一新 Terra 上下文从文件读到原神已完成、蛊真人待办；创建浏览器标签时工具报告 `trusted Node process exited unexpectedly; kernel reset`。worker 按规则 stop，没有采集、重试或覆盖。
- 主审 audit 确认 F 的第三条事件为 `stopped / CUA_KERNEL_RESET`，原神证据完整，任务 terminal、未 finalized。worker 曾将 stop 的 exit 1 误报为“未写停止事件”；该报告已按实际文件纠正。此 CLI 的 stop 故意返回业务失败诊断，不能仅凭 exit 1 推断日志写入失败。

以上保留恢复前的未通过记录。此次从全新 H/G assignment 独立完成最终配方，不合并旧 D/F 的部分成功来补齐资格，不删除失败 job 或改变预期值。

## 打包修正前的成功小批（历史证据）

| 上下文 / 独占 Edge 标签 | 任务与结果 | CLI 成功 |
| --- | --- | ---: |
| terra_qualified_h1 / 928393801 | H：原神完成后 status 正常暂停，蛊真人 pending | 4/4 |
| terra_qualified_h2 / 928393805 | H：新上下文恢复，只处理蛊真人，再 finalize/audit | 6/6 |
| terra_qualified_g / 928393809 | G：独立赛博朋克2077 分片，finalize/audit | 6/6 |

三个世界均由固定适配器读取 3 次，worker 不手写解析器或来源元数据。合计 3 世界、15 身份及物资、75 天赋，共 93 条；三次执行无失败、重试或人工代填。Astra 独占标签 928393798 串行复读选定原文，核对保留 fixture 原文、数据 hash、全部数组与输出，H/G 最终 audit 均 exit 0，pending 为空。

- H 回执 SHA-256：`21f60aadbd41d12cf5b610e8f563b37034d4a22a59b9948a21a3aa6f10fda28f`。
- G 回执 SHA-256：`3737fd8c3a33caa3869dcb9d48e7b270bee80167e77ff8c4275add2556b0d581`。
- 该次 assignment、事件链、回执和逐世界 hash 已保留在机器记录的 `packagingNormalization.priorFinalQualification`；本地 job 文件留在被忽略的 `.rpg02-work/worker-batches/`，不提交运行目录。

独立汇总检查首次把 AST 的 null-prototype 对象与普通 JSON 对象作严格原型比较而失败，写文件前已停止；修正为比较完整 JSON 数据值后同项重跑通过。所有原文、hash、字段和数组顺序校验保留，worker 工具与冻结 Skill 均未改变。这项主审检查纠错不计为 worker 执行失败，原记录已保留。

## 已固化的交付

- `tooling/worker-capture.mjs`：只读 CUA 适配器，注入固定 Acorn 8.18.0 与 Web Crypto。唯一数据库声明、直接对象归属、真实标题、有界窗口和复读校验；不保存完整 HTML。
- `tooling/worker-batch.mjs`：固定模块内路径、assignment/Schema、排他锁、不可覆盖来源、双份确定输出、事件 hash 链、检查点和最终回执。文件恢复不依赖上一 worker 的对话。
- [Skill](../skills/rpg02-selected-content/SKILL.md)：唯一已预演转交包装、首次写权限、独占 Edge 标签、待办项处理及停批规则。能力异常、复制失败、字段漂移、登录/验证码、弹窗、限流、额度争议或校验失败均交回 Astra。

扩展测试为完整世界 14、浏览器适配器 17、批处理护栏 8，共 39 项；加原 96 项共 135 项。Windows directory junction 有真实拒绝测试；普通文件 symlink 创建受本机 EPERM 限制，该分支未实测。原 96 项成功标记不包含 worker 资格结论。

## 后续恢复与范围

本次已完成同一最终配方的两分片、三世界和跨上下文续跑。已停止的 A/C/E/F job 不恢复、不清理或覆盖。实际全量提取必须等 RPG-06 第一版路线完成后，再取得批量授权、对应黄金小批、互斥资源分片及独立页面；本轮不下发全量任务，不建设调度器。新型号、资源类别或页面版本须重新取得对应资格。

当前结构仅覆盖 `worldDB` 直接对象的世界、全部身份/物资与全部天赋。通用池、其他资源类别及隐藏文风/世界书/提示词没有由这组测试取得资格。2 MiB 是格式校验上限，并不保证浏览器输出或 Windows 命令行可无损传递相同大小。工具上限、完整复制或未知结构失败时停批。

raw hash 只证明选中 DOM 字面量；声明前缀和选中范围重读之外的等长页面变化、服务器原文件身份不在证明范围。内容中的价格、任务、死亡重生及 root 保留为数据，权限为空。“一切皆插件”、卡片市场与模镜控制面方向不变。

## 复核与交接

```powershell
node --test --test-reporter=spec tests/worker-capture.test.mjs tests/worker-batch.test.mjs tests/world-source.test.mjs
npm.cmd run verify:rpg02 -- --base a43cfa389e1785a95f04a006ba26550a5a36965e
node tooling/worker-batch.mjs audit --job worker-qualification-i-20260905
node tooling/worker-batch.mjs audit --job worker-qualification-j-20260905
git diff --check
```

本轮新增网站消息探针、外部创作/运行模型调用均为 0；编码子智能体另行记录，历史账本不变。用户已授权资格通过后的 Commit、Push、PR；本文件及机器 `publication:false` 均是发布前快照，实际发布以 Git 提交、远端分支和 PR 元数据为准。Merge、Deploy、Release、Publish 与 RPG-03 未获授权或执行。回退为停止下发该工具/Skill，保留原证据与失败任务，不涉及父仓运行路径。

提交前后使用同一 RPG-02 聚合门禁：固定 base 常量不变，分支、祖先关系、冻结合同、依赖及白名单持续验证，并报告 candidateHead。RPG-01 历史脚本未修改。后继合法提交不再被错误的 HEAD 等值条件拒绝。

## 最终提交字节复验

暂存检查发现黄金 JSON 多余末行，以及边界测试和 worker CLI 各一处混合换行。修正仅改变空白，Acorn token/JSON 值比较一致；仍记录原/新字节 hash 并暂停资格，以新的 I/J 分片重新完成三个全新 Terra 上下文、三世界及 pending 续跑。16 条 CLI 全部成功、无重试，主审重新核对真实 DOM、完整字段、93 项资源、双输出和回执。当前 worker CLI SHA-256 为 `19160f9ce97c5bf2b5d9b35073138edd77f5bf2fedf1d1b9f1c7d0240ae149b6`；Skill 与适配器未改。旧 H/G 成功和所有失败证据均保留，最终资格只绑定 I/J 的提交字节版本。

| 新上下文 / 独占标签 | 操作 | CLI |
| --- | --- | --- |
| terra_final_i1 / 928393833 | I：原神后暂停 | 4/4 |
| terra_final_i2 / 928393837 | I：新上下文仅处理蛊真人，再 finalize/audit | 6/6 |
| terra_final_j / 928393841 | J：独立赛博朋克2077，再 finalize/audit | 6/6 |

I 回执 SHA-256：`e4eaed4e01716a41232c633850698c2a0cb17f6c8b952b0ca216640c13c34c99`；J：`9b9dee7006d08b60cbc6d5677ddea2581815bcd17430b3f0cee7233ddc8b661d`。机器 `finalQualification` 固化这次最终回执；主审本地证据文件 hash 为 `ce85aa5d4e96b00c52a709908836c3c3fbab126173ba68c76eb017cd2081078f`。H/G 与 I/J 是两次成功复演，前者不代替最终字节资格。
