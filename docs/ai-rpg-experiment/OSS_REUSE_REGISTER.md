# 开源复用与许可证登记
核查日期：2026-09-04。用途是管理本次新增的外部 best practice/复用候选，与用户已经完成的原卡法律审计无关。许可证依据官方仓库当前公开主线；尚未固定 release/commit，也未完成完整依赖树、构建产物、模型、权重、数据、素材、插件或服务条款清查，因此没有任何“已批准引入”的结论。
状态词：
- **机制参考**：只使用公开思想，不复制代码、模板、素材或 UI。
- **代码候选**：根许可证允许进一步评估；真正采用前仍需锁定版本并清查实际取用文件及依赖。
- **暂缓**：许可证、控制面重叠、成本或范围不适合第一阶段。
## 1. 提示词、规划与角色
| 项目 | 已核查许可 | 可吸收内容 | 当前处置 |
|---|---|---|---|
| [Re3](https://github.com/yangkevin2/emnlp22-re3-story-generation) | 根 [MIT](https://github.com/yangkevin2/emnlp22-re3-story-generation/blob/main/LICENSE) | 层级计划、相关上下文、候选重排、事实修订 | 机制参考；代码可评估。下载数据、checkpoint、WritingPrompts 派生内容另行核查 |
| [DOC](https://github.com/yangkevin2/doc-story-generation) | 根 [MIT](https://github.com/yangkevin2/doc-story-generation/blob/main/LICENSE) | 详细大纲控制、场景/人物绑定、控制与创造性权衡 | 机制参考；旧模型服务与数据包不进入首阶段 |
| [DOC storygen v2](https://github.com/facebookresearch/doc-storygen-v2) | 根 [Apache-2.0](https://github.com/facebookresearch/doc-storygen-v2/blob/main/LICENSE) | 更新版长篇规划实现的后续研究入口 | 暂缓；未核查模型/数据/运行成本 |
| [IBSEN](https://opendfm.github.io/ibsen/) | 仓库代码 MIT（本轮核查）；数据、戏剧改编、图片/媒体另计 | 导演/演员/玩家权限与玩家介入后重规划 | 机制参考；开放世界和规则密集 RPG 仍需验证 |
| [RoleLLM-public](https://github.com/InteractiveNLP-Team/RoleLLM-public) / RoleBench | GitHub 根目录未发现 LICENSE；RoleBench 数据卡标记 Apache-2.0 | 角色档案、角色知识、说话风格、评测维度 | 论文方法可参考；不复制无许可证仓库内容。数据与人物文本再核权 |
| [Keep CALM and Explore](https://aclanthology.org/2020.emnlp-main.704/) | 相关代码仓根未发现 LICENSE；ClubFloyd 授权描述不等于通用开源许可 | 状态相关的紧凑动作候选 | 仅思想参考，不复制代码、数据或权重 |
| [D&D Dialog Challenge](https://research.google/pubs/dungeons-and-dragons-as-a-challenge-problem-for-artificial-intelligence/) | 本轮只使用论文；未主张数据/代码复用 | 区分下一回合生成与状态预测、玩家/GM分工 | 仅思想参考；数据涉及实际游玩及第三方内容 |
| [Agency Reconsidered](https://dl.digra.org/index.php/dl/article/view/369) | 论文注明个人/课堂使用；商业复制需作者许可 | 玩家意图、系统可行行动与可感知后果的 agency 定义 | 只引用概念与事实，不复制论文或量表 |
| [Doran & Parberry 任务生成研究](https://pcgworkshop.com/archive/doran2011prototype.pdf) | ACM 版权；个人/课堂许可不等于商业复用 | 任务结构、生成时机与世界状态适配 | 机制参考；不能由“可生成”推断“有趣” |
| [Hunicke DDA 研究](https://www.researchgate.net/profile/Robin_Hunicke/publication/220982524_The_case_for_dynamic_difficulty_adjustment_in_games/links/53fb98490cf2dca8fffe800a.pdf) | 本轮未发现开放复用许可 | 可靠反馈、未来难度/节奏调度 | 只引用结论；20 名 FPS 玩家，迁移到 AI RPG 属推断 |
| [PAYADOR](https://github.com/pln-fing-udelar/payador) / [论文](https://www.colibri.udelar.edu.uy/jspui/bitstream/20.500.12008/51156/1/Gon25.pdf) | 代码 GPL-3.0；论文未发现开放许可证 | 开放输入、结构化状态、受限 transformation | 机制参考；不移植 GPL 代码。8 人实验和小功能范围不能证明完整 GM |
| [RoleEval](https://github.com/Magnetic2014/RoleEval) | 当前公开仓未见通用 LICENSE；README 称英文版仍在内部审核 | 中英角色知识与文化分布的窄评测 | 不复制代码/数据；选择题知识不能替代多轮本地语言游玩 |
## 2. 互动叙事与内容工具
| 组件 | 已核查许可 | 可吸收/复用方向 | 边界与处置 |
|---|---|---|---|
| [ink](https://github.com/inkle/ink) | [MIT](https://github.com/inkle/ink/blob/master/LICENSE.txt) | 条件、选择、变量作用域、人工场景 | 代码候选；ink 状态不替代游戏账本 |
| [Inky](https://github.com/inkle/inky) | README 内 MIT | 外部作者预览和编译 | 后续作者工具参考，不在模镜重做完整编辑器 |
| [inkjs](https://github.com/y-lohse/inkjs) | [MIT](https://github.com/y-lohse/inkjs/blob/master/LICENSE.md) | Web/Node 执行 ink 内容 | 代码候选；锁定 ink 编译兼容版本后评估 |
| [Yarn Spinner 核心](https://github.com/YarnSpinnerTool/YarnSpinner) | [MIT](https://github.com/YarnSpinnerTool/YarnSpinner/blob/main/LICENSE.md) | 宿主单一状态、命令/函数、storylets/saliency | 机制参考；具体运行时按组件核查 |
| [Yarn Spinner Unity](https://github.com/YarnSpinnerTool/YarnSpinner-Unity) | MIT | Unity 集成 | 当前 Web/FastAPI 范围不优先 |
| [Yarn Spinner Godot GDScript](https://github.com/YarnSpinnerTool/YarnSpinner-Godot-GDScript) | [YSPL v1.0](https://github.com/YarnSpinnerTool/YarnSpinner-Godot-GDScript/blob/main/LICENSE.md)，含竞争产品及模型训练等限制 | Godot 集成 | 不是 MIT 或通用宽松许可；本阶段不复用 |
| [Twine 作者应用](https://github.com/klembot/twinejs) | [GPL-3.0](https://github.com/klembot/twinejs/blob/develop/LICENSE) | 可视化作者流程 | 只作外部工具参考。作者应用许可不自动决定原创故事文本许可；嵌入格式代码另核查 |
| [Twee 3 规范](https://github.com/iftechfoundation/twine-specs/blob/master/twee-3-specification.md) | 本轮未在规范仓取得明确根许可证 | 纯文本 passage、元数据和交换格式 | 互操作思想参考；不复制规范正文/实现代码 |
| [Tweego](https://github.com/tmedwards/tweego) | BSD-2-Clause | Twee 命令行编译 | 后续作者工具候选，不是 RPG 状态引擎 |
| [SugarCube 2](https://github.com/tmedwards/sugarcube-2) | BSD-2-Clause | 临时/持久变量、passage history 与存档边界 | 机制参考；浏览器历史语义不直接作为 RPG 事务 |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | AGPL-3.0 | 中文触发、递归/条件世界信息的公开文档 | 按用户要求不复制 Tavern 项目；仅使用公开机制思想 |
| [GUMSHOE SRD](https://pelgranepress.com/gumshoe/files/GUMSHOE%20SRD%20CC%20version.pdf) | CC BY 3.0 | 核心线索、非线性场景与资源换额外优势 | 机制/规则候选需归属；不覆盖具体商业设定 |
| [Blades in the Dark SRD](https://bladesinthedark.com/licensing) | CC BY 3.0 | position/effect、后果抵抗、时钟和行为型成长 | 机制/规则候选需归属；设定、NPC、地图、插图、名称/logo 另计 |
| [Failbetter StoryNexus/QBN 说明](https://www.failbettergames.com/news/storynexus-developer-diary-2-fewer-spreadsheets-less-swearing) | 未发现开放内容许可证 | storylet 的状态门控、可扩写性及断奏缺点 | 只作作者一手实践参考，不复制内容或工具 |
## 3. 状态、编排与生成适配
| 项目 | 已核查许可 | 候选能力 | 风险与处置 |
|---|---|---|---|
| [XState](https://github.com/statelyai/xstate) | MIT | 生命周期、阶段允许动作、actor 持久快照 | 代码候选；恢复会重启 invocations，外部调用仍需回执防重。Stately Studio/Cloud 另行处理 |
| [boardgame.io](https://github.com/boardgameio/boardgame.io) | MIT | moves、turns、phases、阶段允许动作 | 机制参考；多人、大厅、网络层对第一阶段过重 |
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | [MIT](https://github.com/pydantic/pydantic-ai/blob/main/LICENSE) | 结构化输出、校验、TestModel/FunctionModel | 代码候选；只放在模镜适配/提案层。重试可能产生新模型调用；Logfire另计 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | [MIT](https://github.com/langchain-ai/langgraph/blob/main/LICENSE) | checkpoint、interrupt、分支和恢复编排 | 机制参考优先；time travel 会重执行 checkpoint 后的 LLM/API，不能当零调用读档 |
| [promptfoo](https://github.com/promptfoo/promptfoo) | [MIT](https://github.com/promptfoo/promptfoo/blob/main/LICENSE) | 数据驱动提示回归、断言、自定义 provider | 后续轻量评测候选；默认遥测、账号信息和部分 hosted inference 路径须显式处理 |
| [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | [MIT](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/LICENSE) | dataset/solver/scorer、多轮评测、重评分日志 | 后续长局评测候选；日志可能含消息，provider/远程存储逐项治理 |
| [TextWorld](https://github.com/microsoft/TextWorld) | 根 [MIT](https://github.com/microsoft/TextWorld/blob/main/LICENSE.txt) | 可接受动作、约束、种子任务和 ground truth | 评测思想参考；原生环境偏 Linux/macOS，部分可选依赖含 GPL 组件，选择依赖图后再判断 |
| [BALROG](https://github.com/balrog-ai/BALROG) | 根 MIT（本轮核查） | 长时游戏代理、环境适配、进度/成功/动作成本 | 后续评测参考；所集成游戏、环境、数据和素材不由根 MIT 重许可 |
| [RPEval](https://github.com/yelboudouri/RPEval) / [论文](https://arxiv.org/html/2505.13157v1) | 论文 CC BY 4.0；当前代码/数据仓未见 LICENSE | 情绪、决策/道德、越界知识的单回合窄回归 | 论文可引用；不复制未授权代码/数据。GPT-4o 生成数据和匿名众包标注另核权 |
## 4. 世界书、检索与长期记忆
| 项目 | 已核查许可 | 候选能力 | 风险与处置 |
|---|---|---|---|
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | MIT | 实体、关系、声明、社区摘要和全局/局部检索 | 后续候选；官方提示索引昂贵。模型、embedding、向量库和导入内容另计 |
| [Mem0](https://github.com/mem0ai/mem0) | [Apache-2.0](https://github.com/mem0ai/mem0/blob/main/LICENSE) | 追加式提取事件关联、语义/BM25/实体混合检索 | 第一阶段之后的轻量候选；Platform 与 OSS 能力、dashboard 文案存在边界，不能承诺完整控制台 |
| [Letta Code](https://github.com/letta-ai/letta-code) | Apache-2.0；许可证末尾排除 Letta 名称、标志、图片和 ASCII art 等品牌资产 | 常驻 system/按需文件、Git 版本记忆、后台整理 | 机制参考；完整 agent harness 与模镜重叠，不整体嵌入。旧 Letta V1 server 已归档且官方不再维护 |
| [Graphiti](https://github.com/getzep/graphiti) | [Apache-2.0](https://github.com/getzep/graphiti/blob/main/LICENSE) | 时态实体关系、来源 episode、事实有效期、混合检索 | 在多跳时间关系需求被证实后评估；Zep 专有引擎/托管不是 Apache Graphiti 的等价开放实现 |
| [Generative Agents](https://github.com/joonspk-research/generative_agents) | [Apache-2.0](https://github.com/joonspk-research/generative_agents/blob/main/LICENSE) | 观察、相关/近期/重要性检索、反思与计划 | 研究原型；README 单列多方美术来源，素材不能随根许可打包。成本数据是历史实验，不作今天预测 |
## 5. 安全与内容供应链资料
| 参考/组件 | 已核查许可 | 候选用途与边界 |
|---|---|---|
| [OWASP GenAI/LLM Top 10](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications) | 内容 CC BY-SA 4.0 | 间接提示注入、输出处理和过度代理权的机制参考；引用/改编需署名，不能把提示过滤当成安全边界 |
| [DOMPurify](https://github.com/cure53/DOMPurify) | Apache-2.0 或 MPL-2.0 双许可 | 只有确需富 HTML 时才作代码候选；锁定受支持版本、保留相应 notices，仍需 CSP/Trusted Types 与网络资源限制 |
| [CommonMark spec](https://github.com/commonmark/commonmark-spec) | 规范 CC BY-SA 4.0；测试/工具许可另列 | Markdown 语法和测试参考；实际 parser 与 renderer 单独核查，Markdown 本身不净化 HTML/URL |
| [JSON Schema spec](https://github.com/json-schema-org/json-schema-spec) | BSD-3-Clause 或 AFL-3.0 | 内容 schema/dialect 参考；实际 validator 的许可证另查 |
| [SLSA spec](https://github.com/slsa-framework/slsa) | Community Specification License 1.0；部分早期材料另有 Apache-2.0 | 来源/验证思想；完整签名构建链对第一阶段过重，配套工具不由规范许可覆盖 |
| [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | NIST 公共信息规则，第三方材料可能另有权利 | 风险目录、TEVV、事件和内容来源参考；跨组织治理动作不直接变成单机 RPG 要求 |
## 6. 专有服务与标准资料
以下只作公开设计参考，不进入代码复用清单：
- [NovelAI Lorebook](https://docs.novelai.net/en/text/lorebook/) 与 [Context Viewer](https://docs.novelai.net/en/text/editor/advancedsettings/)：激活、位置、预算、裁剪与可解释装配。
- [AI Dungeon Story Cards](https://help.aidungeon.com/faq/story-cards)、[Plot Essentials](https://help.aidungeon.com/faq/plot-essentials) 与 [上下文装配](https://help.aidungeon.com/faq/what-goes-into-the-context-sent-to-the-ai)：常驻/动态上下文分层和玩家编辑能力。
- [Microsoft HAX](https://www.microsoft.com/en-us/haxtoolkit/ai-guidelines/)：支持纠正、解释原因、说明后果和全局控制。
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) 与 [Xbox Accessibility Guidelines](https://learn.microsoft.com/en-us/xbox/accessibility/guidelines)：键盘、回流、状态消息、时间内容、反馈和可撤销操作。
- [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/)：长上下文位置效应；引用论文，不推断所有当前模型表现相同。
- [PXI 官方指南](https://playerexperienceinventory.org/docs)：10 构念玩家体验量表。官方称开放、免费使用；保持原题和量尺才可声称使用已验证 PXI，翻译/删改后需重新验证并按实际名称报告。
- [MT-Bench / LLM-as-judge 论文](https://proceedings.neurips.cc/paper_files/paper/2023/file/91f18a1287b398d378ef22505bf41832-Paper-Datasets_and_Benchmarks.pdf)：位置、冗长、自偏好和 pairwise 交换顺序等裁判偏差；论文引用不等于代码/数据复用授权。
## 7. 真正复用前的记录要求
采用候选时，应在对应轮计划中登记：
1. 具体仓库、组件、tag/commit、取用文件和许可证原文哈希。
2. 必须保留的版权/许可/NOTICE、修改说明及发布物中的放置位置。
3. 直接与传递依赖、可选 extra、生成代码和构建产物的许可证。
4. 模型、权重、训练/评测数据、人物文本、图片、音频、字体、示例内容各自来源。
5. 云服务、企业功能、遥测、远程推理、日志与数据外发路径。
6. 与模镜模型/记忆/权限控制面的职责冲突、替换成本与退出路径。
这是一条工程证据要求，不是本轮的库选型或执行门禁。当前所有候选保持未采用状态。

## 8. 插件市场与卡片市场的登记合同
后续若形成插件市场，每个插件至少登记：
1. 插件 ID、版本、核心兼容范围、入口、能力与权限；网络、文件、数据、模型调用和 UI 插槽分别声明。
2. 设置 schema、直接/传递依赖、许可证/NOTICE、代码与发布物 hash、来源仓库及构建回执。
3. 安装、显式授权、启用、停用、升级、迁移、回退和卸载生命周期；失败隔离、降级、遥测及数据删除边界。
4. 模型、数据库、向量库、云服务、权重、数据和素材分别登记，不因插件代码开源而推定一起获准。
5. 不保存模镜密钥；模型、预算、长期记忆和安全策略继续由模镜控制面治理。

后续若形成卡片市场，每个卡包至少登记：
1. 来源类型：用户提供的 Tavern 卡片转化、协助编写或未来 UGC；以及作者/贡献者、原始格式、原件 hash 和取得时间。
2. 转换器 ID/版本、字段映射、丢失/新增/规范化变更、转换回执及目标卡包 schema 版本。
3. 角色、世界、身份、天赋、背景、文风、世界书、开场、示例、媒体和字体各自的来源、许可证/许可记录及 hash。
4. 核心兼容范围、必要/推荐插件、缺失时降级和禁止静默安装声明。
5. 卡包默认仅含受控数据；任意脚本、工具调用或其他执行能力必须拆分成单独插件并走插件权限审核。

转换器代码的许可证、输入卡片内容的授权和 Tavern 平台代码的许可证分别核查。转换数据不等于复用 Tavern 平台实现，不能将平台代码或依赖随卡包带入。