# RPG04 厂商提示词研究（待逐项评审）

观察日期：2026-09-07（America/Phoenix）

## 范围与证据边界

本文只整理 OpenAI、Anthropic、Google 当前第一手文档中与 RPG04 原版系统提示词相关的依据。研究对象是提示词的职责、结构、上下文和评测方法，不是原版提示词的修改稿，也不代表任何方向已经获准采用。

三家文档面向不同模型和产品。厂商自己的实验结果只能说明其指定模型、上下文和任务；分隔符、角色标签或提示词措辞也不是安全边界。最终设计仍需按目标模型、真实卡片和运行时契约验证。

## 可交叉支持的结论

| 结论 | 第一手依据 | 对原版条款的对应 | 限制与反例 |
| --- | --- | --- | --- |
| 指令应明确目标、优先级、约束和输出，减少冲突、重复及未定义术语。 | OpenAI 要求按模型给出明确指令并用评测迭代；Anthropic 强调清晰直接并明确期望输出；Google 的检查清单要求排查歧义、冲突、重复、无关要求和未定义术语。 | 原版同时要求隐藏推理与输出思维链、要求最顶端不同内容，以及 STM 满 7 条前后的处理表述，需要逐项判定优先级和真实意图。 | “更短”本身不是目标；复杂任务仍可能需要具体约束、数据和成功条件。
| 将主持职责、玩家输入、背景数据、当前状态、示例和输出格式分区，有助于解释边界。 | OpenAI 区分高权限 instructions/developer 与 user 输入，并建议用分隔符；Anthropic 用一致的 XML 标签分开 instructions、context、examples、input；Google 给出 objective、instructions、constraints、context、examples、output format 等组件。 | 与 `HOST_TEMPLATE` 的主持权限、玩家自主权、非可信卡片/世界书/文风/历史、当前状态和结构化输出分层相符，可作为审阅原版混合区块的比较基准。 | XML、Markdown 或标签只帮助模型解析，不能阻止不可信内容越权；实际权限仍由宿主装配、验证和运行时执行。
| 示例应服务于明确的失败模式，并与文字规则一致。 | OpenAI 对推理模型建议先零样本，复杂输出再用严格对齐的少量示例；Anthropic 和 Google 都把示例作为复杂、细微或格式任务的可选手段。 | 原版大量固定面板可先转成可测试的输出要求；是否需要正反例，应由目标模型在卡片场景中的失败证据决定。 | 示例可能固化偶然风格或与规则冲突；三家均未证明某组 RPG 示例可跨模型通用。
| 不应把强制展示详细思维过程当成跨模型通则；内部推理与对外输出要分别审阅。 | OpenAI 明确称推理模型通常不需“逐步思考”提示；Anthropic 说明 thinking 行为随 Claude 型号和版本变化，通常优先一般性指令；Google 要求比较内置 Thinking 与显式推理步骤。 | 原版 `HideReason`、`</think>`、思维链置顶与页面输出纪律需要拆成“模型内部处理要求”和“用户可见结果要求”后逐项审批。 | Google 仍展示显式推理用例，Anthropic 也保留特定模型的手动推理方案，因此不能宣称显式步骤在所有任务都无效。
| 长上下文容量不等于所有资料都应每轮注入；相关性、位置和检索质量仍需测量。 | OpenAI 提醒上下文窗口因模型而异，并在 GPT-4.1 实验中观察到检索项增多和全上下文推理会退化；Anthropic 建议长文档置前、查询置后，并报告其内部长上下文测试收益。 | 原版每轮完整静态状态、五项商品和全部记忆的要求，应与 RPG04 计划中的当前状态、相关世界书/文风/历史选择分别测量。 | OpenAI 的位置与分隔符结果以及 Anthropic 的百分比都是模型特定实验，不能直接规定本卡顺序或截断阈值。
| 提示词变更要用代表性样例和多维指标验证，不能凭单次观感决定。 | OpenAI 建议固定模型快照并建立测试/评测套件；Anthropic 要求具体、可测、任务相关的成功标准和多维评测；Google 提供逐例与聚合、点式与成对比较指标。 | 后续可测玩家决定权、无进展回合、状态提案合法性、世界设定依据、文风一致性、格式遵循、延迟与成本。 | 自动评分器可能缺乏细微判断或继承偏差；厂商通用指标不等于 RPG 叙事质量，需要人工样例和运行时断言共同校准。

## 来源记录

### OpenAI

- [Prompt engineering](https://developers.openai.com/api/docs/guides/prompt-engineering)：实际读取正文。支持模型类型与快照会改变提示效果、角色权限分层、相关上下文、少样本示例以及先建测试和评测。对应原版的职责混排、静态资料重复和固定格式。限制是页面同时包含型号及产品特定建议，不能全部外推。
- [Reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices#how-to-prompt-reasoning-models-effectively)：实际读取目标章节。支持对推理模型使用简单直接指令、避免强制思维链、先零样本后按需加严格对齐示例。对应原版强制可见思维链。限制是该建议针对 OpenAI 推理模型。
- [GPT-4.1 prompting guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-4.1)：实际读取长上下文与分隔符章节。支持相关性、位置和检索规模需要实测。其 XML/JSON 和长上下文数据仅作为 GPT-4.1 实验结果，不作为跨模型规则。

### Anthropic

- [Claude prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)：实际读取清晰指令、XML 分区、长上下文和 thinking 章节。支持明确期望、分开数据与指令、把长文档查询放到资料之后，并按型号选择推理方式。对应原版职责、世界信息、记忆与思维链区块。限制是页面明确区分 Claude 型号和版本。
- [Develop tests and evaluations](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests)：实际读取评测标准和方法正文。支持具体、可测、多维、任务相关的成功标准，以及代码评分、人工评分和模型评分的取舍。对应后续原版条款逐项验收。限制是模型评分需要校准，人工评分成本较高。

### Google

- [Prompt design strategies](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/prompts/prompt-design-strategies)：实际读取组件、检查清单和最佳实践正文。支持任务驱动迭代、结构化分区、冲突检查、明确输出、按需示例，以及比较内置 Thinking 与显式步骤。对应原版相互冲突的开头、推理、面板和记忆要求。限制是该页属于 Gemini Enterprise Agent Platform，组件模板不是强制协议。
- [View evaluation results](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/eval-python-sdk/view-evaluation)：实际读取逐例、聚合、点式和成对结果说明。支持对提示变体比较 instruction following、coherence、fluency 等维度。对应后续候选版本比较。限制是这是 Google 评测 SDK 的结果模型，不能自动证明 RPG 行为正确。

## 待逐项审批的研究方向

1. 为每条原版要求指定唯一职责和优先级，先列出冲突对，再决定保留、删除或改写；当前不作决定。
2. 将绝对禁止语句改成可观察的期望行为与必要例外，只在真实失败样例表明确边界后提出具体文字。
3. 将宿主规则、玩家输入、卡片/世界书/文风、当前状态、历史和输出契约分层；动态资料按相关性装配，并用运行时验证承担真正的权限与状态边界。
4. 不要求模型公开详细内部推理；应否提供简短依据、审计字段或不确定性，需按目标模型能力和产品展示目的另行审批。
5. 为每个候选修改先定义固定夹具、目标模型快照、成功指标和反例，再比较正确性、叙事质量、延迟与成本；模型评分只作经校准的一个信号。

## 尚缺证据

本文没有提供世界书或文风资源的真实形式样例，也没有验证它们在目标网站中的注入位置和优先级。需等待至少两个实探样例完成后，才能制定具体修改合同。经济、死亡、继承、等级、字数、选项数量、商店频率和记忆引擎均未由这些厂商文档证明应当硬编码。
