# RPG-04 世界书与叙事研究候选

核查日期：2026-09-07。本文只提供后续逐项审批的依据；未看到两组探针取得的原卡文风/世界书样例，因此不定最终优化格式、不写替换示例，也不把候选倒灌提示词。第一版保持零可选插件、无向量/图检索/记忆宫殿；任务、经济、死亡与继承只是目标卡内容，不提升为通用主体。

## 当前合同边界

卡包资源合同（实际导出位于 `src/index.mjs`）已有带 `instruction` 的 `styles`，opening 通过 `styleRefs` 选用；每项资源都有 `sourceRefs`。`worldbookEntries` 自带 `player/host/shared` 可见性、`content/tags/worldRefs`；`context/contracts.mjs` 会拒绝把 host 条目设为 required，并在装配回执及公开来源中排除 host 条目。context profile 负责场景/资源引用、世界与资源别名，`all/any/not` 关键词，`eq/ne` 状态条件，必选项、优先级、冲突组、替代关系，以及输入/lore/history/output 预算；回执记录纳入、未命中、越界、预算排除、被替代及来源。尚未实现的是每 NPC 知识视角、时间有效性等更细粒度语义；是否需要扩展合同，须等样例证明后逐项审批。

## 可供逐项审批的设计候选

1. **原卡拆分而不改写语义。** 每条围绕单一人物、地点、规则或事件，标题/组织备注不视为模型可见事实，正文再次点名实体；事实、叙述文风、例句分别存放并标来源。AI Dungeon 与 NovelAI 都明确“名称/标题仅供组织，模型只见条目正文”。局限：平台机制不是本任务最优性的证据；需用两组原卡检查拆分是否破坏伏笔或韵律。
2. **触发与范围可解释。** 先用 `sceneRefs/resourceRefs` 和显式别名，再用短语关键词、AND/NOT 与状态条件；中文姓名、称号、旧名逐项登记，避免单字泛触发。每次用现有 receipt 说明命中、排除、替代和来源。局限：关键词会漏掉语义改写；第一版先测漏召回，不能据此提前引入向量或图。
3. **沿现有可见性继续细分知识视角。** 先使用卡包现有 `player/host/shared` 可见性及 context 对 host 条目的 required/receipt 隔离，保留每项 `sourceRefs`；UI 隐藏不等于模型不可见。每 NPC 知识范围、已揭示阶段和时间有效性只是待样例验证的候选，不能预设必须扩 schema。局限：资源级可见性不能独自表达每个 NPC 在每一时点知道什么。
4. **文风说明与事实分离。** 文风候选写成可观察约束（视角、人称、句长/段长、感官密度、对话比例、禁用习惯），例句仅作为风格证据并标明“不可续写为事实”；角色稳定核拆成身份/动机/知识边界/语言习惯，可变情绪和关系仍由场景事实驱动。局限：RoleLLM 研究的是角色模仿与问答，不证明开放式 GM 的长期一致性。
5. **玩家自主权作为生成验收。** 输出只推进玩家明确尝试动作的直接后果，不新增玩家台词、内心、目标或不可逆决定；建议行动是非穷尽提示。记录“玩家原话→叙事承认的意图→可见后果”，人工检查是否出现代演。局限：D&D 论文说明角色/DM 回合与状态预测是独立挑战，未给出本项目的最佳提示词。
6. **节奏与重复按场景验证。** 使用“局势建立→玩家决定→后果→新可行动点”检查单回合；统计连续回合的同义开场、形容词、NPC 反应、总结复述和无状态变化段落。长高潮允许连续场景，不强制切成短 storylet。局限：Failbetter 明言 storylet 的 setup-choice-result 节奏会显得断奏；其 QBN 经验不能直接移植为引擎。
7. **预算与位置做实测。** 先保留主持权限、当前场景、玩家本轮输入和输出预算，再按 required/priority/冲突规则纳入 lore；优先整条排除而非截断事实。对相同探针做条目顺序和预算消融，检查关键事实召回与文风漂移。局限：Lost in the Middle 基于多文档问答/键值检索，不能推出固定跨模型排序，只支持必须测位置敏感性。

## 后续样例到齐后的验证矩阵

- 语义：逐条列原句、拆分后事实、触发条件、来源；检查新增事实、遗漏、冲突和过期信息。
- 视角/自主：标注叙述人称、自由间接引语、玩家代演、NPC 知识越界与未经授权的不可逆推进。
- 角色：为主要人物抽取稳定核和可变状态；跨至少 3 个相邻回合核对动机、称谓、知识、语气与因果变化。
- 节奏/重复：人工盲评“可行动点、推进量、复述量、句式重复、场景密度”，并保留具体句段证据；字数和格式通过不等于文字质量通过。
- 世界书：构造命中、漏召回、误触发、冲突替代、预算排除和隐藏真相六类离线用例，核对 receipt；不得把离线命中称为真实生成质量。
- 对照：所有优化一次只改一项，用同一原卡片段、玩家输入、模型配置和预算比较；样本少时只报告观察，不宣称普遍提升。

## 已打开的一手来源与使用边界

1. **NovelAI Lorebook 官方文档**：https://docs.novelai.net/en/text/lorebook/ （访问 2026-09-07）。支持标题不进上下文、entry text、大小写不敏感 key、AND key、Always On、隐藏、搜索范围、插入顺序、token budget/reservation/trim 等机制。不能证明这些默认值适合中文或本项目。页面未声明开放复用许可；只参考思想与字段语义，不复制文案、UI 或实现。
2. **AI Dungeon “What goes into the Context” 官方帮助**：https://help.aidungeon.com/faq/what-goes-into-the-context-sent-to-the-ai （访问 2026-09-07）。支持 required/dynamic 分区、Story Cards/历史预算与可查看装配顺序。具体 70%/25%/50% 是其产品策略，不作为本项目固定比例。专有产品文档，未见开放许可；只引用机制事实。
3. **AI Dungeon “What are Story Cards?” 官方帮助**：https://help.aidungeon.com/faq/story-cards （访问 2026-09-07）。支持 trigger 可来自玩家或 AI 输出、名称/Notes 不给模型、Entry 才进入上下文、简洁自然语言及前后位置提示。其“最佳实践”是产品经验，不是对本任务的对照实验。专有文档，未见开放许可；不复用内容。
4. **Google Research / ACL 2022, D&D as a Challenge Problem**：https://research.google/pubs/dungeons-and-dragons-as-a-challenge-problem-for-artificial-intelligence/ （访问 2026-09-07）。正文摘要支持将角色/DM 回合生成、角色内外话语、游戏状态预测和人评“可信/有趣”分开测。不能证明玩家自主禁令或具体 UI。ACL 2022 论文通常按 ACL Anthology 的 CC BY 4.0 发布；本项目只转述结论，复用数据/正文前仍需核对论文页许可。
5. **RoleLLM, Findings of ACL 2024**：https://aclanthology.org/anthology-files/anthology-files/pdf/findings/2024.findings-acl.878.pdf （访问 2026-09-07）。支持区分角色档案知识、role-specific QA、system instruction/retrieval 与说话风格模仿，并提供角色级 benchmark。不能证明游戏世界状态、长期因果或玩家自主。论文为 CC BY 4.0；仅转述研究结构，若复用其数据集/代码须分别核对仓库许可证与上游角色文本权利。
6. **Lost in the Middle, TACL 2024**：https://aclanthology.org/2024.tacl-1.9/ （访问 2026-09-07）。支持长上下文中相关信息位置会显著影响问答/键值检索表现，因而需要位置与预算消融。不能证明固定首尾模板或中文叙事效果。论文为 CC BY 4.0；只引用结论。
7. **inkle, Writing with ink 官方文档**：https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md （访问 2026-09-07）。支持内容/标签分离、选择文本与输出文本可分、branch/rejoin、条件与 seen count，以及按 knot/stitch 组织场景。不能证明应采用 ink 或确定性剧情树。仓库 MIT；若未来复用代码/实质文档须保留版权与许可，本轮只参考思想且不新增依赖。
8. **Failbetter StoryNexus Developer Diary #2**：https://www.failbettergames.com/news/storynexus-developer-diary-2-fewer-spreadsheets-less-swearing （访问 2026-09-07）。支持 state-gated storylet、选择后显式状态变化，也明确短循环会使长戏剧场景断奏。是官方设计回顾，不是同行实验。未见开放许可；不复制文本、世界观或实现。

## 许可与资源边界

目标原卡沿用户已完成的法律审计与作者书面授权边界提取或重写；真实交互取得的获授权内容可按该授权原样复用或改写，无需再次审计原卡法律。本文新增的第三方专有平台资料仅作机制研究，开放论文只作可归因转述；它们的许可不得与目标原卡授权混淆。ink 即使为 MIT，本轮也不引入代码或依赖。SillyTavern 主代码为 AGPL-3.0，本轮不读取、复制或改写其代码；如未来只参考官方文档机制，也必须独立实现并另行做许可证审查。本轮不发布；未来实际复用新增第三方材料时，仍分别核对其许可。
