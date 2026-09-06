# AI RPG 实验线：恢复入口

更新日期：2026-09-05。当前路线版本 v7，保留六轮结构及“一切可选皆插件”的双市场方向。来源任务：01a06d11-845b-7511-b476-64bbe034b119。

**RPG-01/02 已验收，RPG-02 的 PR #361 已合并；RPG-03 已完成实现与真实验收，待用户人工验收，claimAllowed=false。** 官方 Luna 经隔离模镜控制面完成认证、两次连续回复与提交、恢复和流式取消。用户追加后本轮总额度 5/5 全部消耗，历史失败保留。模块聚合 230 项、父仓回归 53 项通过。RPG-04 至 RPG-06 尚未开始；当前不是完整可玩游戏，全量提取仍留待 RPG-06 后另行授权。

RPG-03 恢复先读 [本轮计划](RPG03_PLAN.md)、[机器状态](../../experiments/ai-rpg-engine/docs/RPG03_STATUS.json)、[真实调用账本](../../experiments/ai-rpg-engine/docs/RPG03_CALL_LEDGER.json)、[最终真实回执](../../experiments/ai-rpg-engine/docs/RPG03_REAL_ACCEPTANCE.json) 与 [验收记录](../../experiments/ai-rpg-engine/docs/RPG03_ACCEPTANCE.md)。两个真实验收实例已停止，存储和证据保留；未 Commit、Push 或创建 PR。历史失败和离线修复记录不覆盖最终真实回执，也不被删除。下方 RPG-02 测试与发布前状态属于历史交付记录。

## 已验收的 RPG-02 交付与边界

RPG-02 固定基线为 `a43cfa389e1785a95f04a006ba26550a5a36965e`，独立工作区 `C:\tmp\modelmirror-ai-rpg-rpg02`，分支 `codex/ai-rpg-rpg02-content`。只改动 `experiments/ai-rpg-engine/**` 与本研究目录。RPG-01 四种 `0.1.0` 合同、根接口与 28 项原测试保持冻结；私有包为 `0.2.0`，新增纯 `/content` 子入口。

真实取样包括蛊真人、Minecraft、四项身份、八项天赋，共 14 项记录与 18 个原文字面量片段。配合四份身份物资和九项自主补写示例，共编译 27 个资源、两个开局组合。完整玩家保留五项天赋，激活由独立配置逐项指定；虚构 root 的运行权限始终为空。

正常卡片真实交互中取得的材料可原样复用或说明后优化；未显示的文风等内容可以自主模仿或重写，分别标记 extracted、derived、authored。原始片段与 hash 有回执，最小 HTML 载体为自行构建。完整原 HTML 未保存，DOM 快照 hash 不冒充服务器原文件 hash。重要但未显示的逻辑可在既有授权额度内另行有限探查，无法取得则重写；本轮没有使用网站消息探针或外部模型调用。

96 项测试通过：原合同 28、边界 7、内容 43、归档 18。聚合门禁为 `RPG02_AUTOMATED_GATES_OK`；真实 CLI 编译、打包、校验、解包通过，黄金重放的输入/成员/归档 hash 一致。补写内容仅用于转换验证，叙事质量、运行时、模镜接入、提示词编排、检索和 UI 未验收。

## Skill 两世界扩展验证

用户追加授权的两例验证已完成：蛊真人取得完整世界对象、5 身份及物资、30 天赋；另一世界选赛博朋克2077，取得完整世界对象、5 身份及物资、15 天赋，共 57 条源资源记录。初次独立试跑发现旧 Skill 仅能重放代表卡包；现已补充独立的完整世界源资源提取流程，14 项扩展测试和原 96 项门禁通过。Sol 按更新 Skill 独立复跑，两组结果与 Astra 输出逐字节一致。

详见 [扩展验证报告](../../experiments/ai-rpg-engine/docs/RPG02_SKILL_GENERALIZATION.md)、[机器回执](../../experiments/ai-rpg-engine/docs/RPG02_SKILL_GENERALIZATION.json) 与 [Skill](../../experiments/ai-rpg-engine/skills/rpg02-selected-content/SKILL.md)。全量范围仅指所选世界对象内的所有字段、身份及天赋；通用天赋池独立，隐藏世界书/文风/提示词未取得。原代表卡包、27 个编译资源及冻结合同不变，本次源资源 JSON 不冒充新增可玩卡包。该两例阶段未新增探针/外部模型调用，未启用 Luna 或进入 RPG-03，仍待人工审阅。

## Luna 单世界最小验证

在上述两例之后，用户另授权 Luna 完成一个新世界。现已取得原神的完整世界对象、5 身份及物资、30 天赋，共 36 项；Luna 完成浏览器捕获及离线提取，主审原站复读 hash 和最终两份输出均匹配，原 96 项门禁通过。

评估结论为 **资源验证通过，流程未全部通过**：首轮有浏览器会话占用与检查点路径错误，经 Astra 修正后重试；随后发现 capture 元数据被覆盖、最终回执未记录早期输出。不能据此直接启动无人值守量产。这是当次试跑结论；其后的新增护栏、最终配方与按型号授予的小批资格见下方追加门槛，原失败记录保留。详见 [Luna 评估](../../experiments/ai-rpg-engine/docs/RPG02_LUNA_TRIAL.md) 与 [审计回执](../../experiments/ai-rpg-engine/docs/RPG02_LUNA_TRIAL.json)。

用户另提供“提示词可引导站内模型输出疑似隐藏系统提示词/部分世界书”的实测观察，已记录在审计第 17 节。该观察和已取得片段均非隐藏原文的权威证据；后续轮次再独立计划、交叉核对并自主优化，本轮未执行该项探查。

## 阅读顺序与权威

1. [交付基线](BASELINE.md)：首版核心、卡片特化扩展与模镜治理约束。
2. [双市场](PLUGIN_CARD_MARKETS.md) 与 [四象限](BOUNDARY_QUADRANTS.md)：必要核心、可选插件、卡片内容的归属。
3. [六轮路线](ROUND_ROADMAP.md)：轮级目标、依赖与实际状态；具体批次在每轮独立计划制定。
4. [AI RPG 双轨研究](BEST_PRACTICES.md) 与 [开源登记](OSS_REUSE_REGISTER.md)：候选参考；研究广度不等于实施范围。
5. [审计记录](AUDIT.md)：直接观察、历史失败、边界修订、已验证事实与限制。
6. [探针账本](PROBE_LEDGER.json)：额度唯一计数来源；发起新探针前必须读取，不借用或重置历史额度。
7. [RPG-02 计划](RPG02_PLAN.md)、[模块说明](../../experiments/ai-rpg-engine/README.md)、[验收记录](../../experiments/ai-rpg-engine/docs/RPG02_ACCEPTANCE.md)、[机器状态](../../experiments/ai-rpg-engine/docs/RPG02_STATUS.json)：当前交付的接口、门禁和消费边界。
8. [来源回执](../../experiments/ai-rpg-engine/fixtures/rpg02/source-capture.json)、[黄金回执](../../experiments/ai-rpg-engine/docs/RPG02_GOLDEN.json)、[模块依赖登记](../../experiments/ai-rpg-engine/docs/RPG02_THIRD_PARTY.json)、[操作 Skill](../../experiments/ai-rpg-engine/skills/rpg02-selected-content/SKILL.md)：恢复与独立重放所需证据。
9. [冻结合同](../../experiments/ai-rpg-engine/docs/CONTRACTS.md)、[RPG-01 验收](../../experiments/ai-rpg-engine/docs/RPG01_ACCEPTANCE.md)、[RPG-01 状态](../../experiments/ai-rpg-engine/docs/RPG01_STATUS.json)：历史首轮交付。
10. [初审快照](references/INITIAL_AUDIT_2026-09-04.md)、[历史资源统计](RESOURCE_INVENTORY.json)、[角色样本](references/PLAYER_CARD_SAMPLE.md)、[MANIFEST](MANIFEST.json)：原始资料与现行文档 hash。

历史初审第 131 行的 AI_RPG_PROBE_LEDGER.json 是旧交付位置；本研究目录的 PROBE_LEDGER.json 是当前唯一账本。历史资源统计不代表这轮实际迁移数量，初审快照不覆盖最新用户决定。

## 恢复时必须保留的决定

- 两个中心始终是卡片主体/内容，以及模镜治理的运行/交互框架。核心链路为资源 → 内容编译 → 自主上下文 → 模镜调用 → 叙事/信息模块 → 玩家继续交互。
- 任务经济、卡内存读档、死亡重生、离开结算与跨世界继承是目标卡附加语义，不独立成轮、不硬编码为通用核心规则。
- 第一版须零可选插件运行。后续向量/图检索、记忆宫殿、自动总结、复杂规则、扩展 UI、创作和转换等所有可选增量进入插件市场，由用户自主选择、授权和启停；首版不建设市场产品。
- 其他卡片、世界书、文风等内容进入卡片市场，来源为用户提供的 Tavern 资源转化、协助编写及未来 UGC；内容转换不引入平台实现。
- 模型、凭据、预算、权限、会话与记忆由模镜治理，当前 `/plugins` 不能当成已实现的新插件宿主。
- Astra 主导首次浏览器交互与异常处理；有状态页面串行掌控。Sol 承接范围明确的离线实现、核对和 Skill 固化；Luna 仅在后续量产另获授权、冻结流程、黄金小批和互斥分片后启用。本轮没有量产调度器或并发浏览器 worker。
- 历史浏览器 `data:` 转存拒绝与运行环境 `EPERM` 路径已停止，不作为后续 fallback。选定片段证据已独立完成，不需要用户提供完整原文件。
- 首轮开发历史额度仍为提交 0、完成 0、剩余 20；RPG-02 新消息探针和外部模型调用均为 0。编码子智能体与网站探针分开。
- 用户已明确授权资格收尾后的 Commit、Push、PR；文档及 publication:false 是发布前历史快照，实际动作以 Git/PR 元数据为准。Merge、Deploy、Release、Publish 与 RPG-03 未获授权或执行；RPG-03 必须独立计划。

## 交付与防漂移

本地审阅产物保留在模块被 Git 忽略的 `.rpg02-work/delivery-rpg02-20260905/`：卡包目录、ZIP 与回读目录。ZIP 为 32827 字节，SHA-256 `b9e4948208e91813732fe1cf8f50db2b20ee41dc05ff7bb990467b3e5d7f2b41`。固定输入可离线重放；该 hash 不证明网站当前未变。

RPG-03 消费卡包/玩家配置，RPG-04 消费文风/世界书/来源索引，RPG-05 消费配置结果/诊断。RPG-02 没有实现上述轮次的行为，补写示例也不代表原站隐藏提示词。

改变核心或卡片特化边界时更新 BASELINE；改变内容/功能归属时更新 PLUGIN_CARD_MARKETS；路线变化更新 ROUND_ROADMAP；新观察更新 AUDIT；探针先登记账本。研究候选只有被对应轮计划明确采用后才进入实施。现行文档由 MANIFEST 管理 13 项 hash 和字节数，清单不包含自身；文档完整性不能替代运行或人工验收。

## 追加的低级 worker 量产门槛

[低级 worker 量产门槛](../../experiments/ai-rpg-engine/docs/RPG02_WORKER_GATE.md)已通过，机器状态为 `qualified_small_batch`，合格型号仅为 `gpt-5.6-terra`。三个新上下文按最终冻结 Skill 完成 I/J 两个互斥分片、三个世界和 pending 续跑；16 条 CLI 全部成功，无重试。原 96 项＋扩展 39 项及正式 Skill 检查器通过。

Luna 的流程/传输失败、Terra 旧配方失败与 F 的工具中断均保留。新 I/J 任务独立完成全部门槛，主审复读真实 DOM 并核对 3 世界、15 身份/物资、75 天赋，共 93 项的完整字段、raw/data hash、零损失、空权限、双输出及最终回执。资格只覆盖当前可见 worldDB 直接对象，不代表 Luna、通用池或隐藏内容已合格，也不保证全站吞吐或无人值守可靠率。全量安排在 RPG-06 第一版路线完成后，并另行授权；其他资源类别与 RPG-03 未启动。所有可选功能继续走插件市场，卡片资源走卡片市场。

提交前暂存检查发现局部混合换行后，已进行 token/JSON 值等价的空白修正、重新冻结，并重跑完整 I/J 小批。H/G 成功及所有失败作为历史证据保留；最终资格绑定当前 CLI 字节，详见门槛报告“最终提交字节复验”。
