# RPG-04 文风与世界书资源格式提案

状态：`S01`、`S02`、`L01`与`L02`均已获用户批准，均为 `approved_pending_application` 且尚未应用。世界书差异、五卡增量与防误拒答研究已记录在 [实施前补充审阅](RPG04_SUPPLEMENTAL_REVIEW.md)；P24/P25仅进入候选措辞初审，不构成采用批准。本文不修改 prompt、fixture、schema或运行时，也不把模型返回的小说概述认证为原著事实。

## 证据现状

| 观察 | 已取得内容 | 能支持什么 | 不能支持什么 |
| --- | --- | --- | --- |
| 蛊真人文风（WEB10） | “黑暗修仙与逻辑智斗”，冷峻、理智，重情报、资源、杀招和心理博弈 | 文风资源可用“基调＋具体描写重点”的短自然语言 | 后端原始字节、隐藏资源结构；同答复中的小说事实与等级未经核实 |
| Minecraft 文风（WEB13） | 孤独、自由、创造力，重方块纹理、挖掘声、空灵音乐、建家成就 | 两世界共同形式确有对照样例 | 单次返回不证明触发方式或跨模型稳定性 |
| 世界书（WEB02/07/09/10/11） | 五次均未取得可认证的条目正文与触发配置；WEB11 还撤回了 WEB10 的“世界书”称呼 | 已达到用户批准的自主编写 fallback 门槛 | 不能断言原卡没有世界书；WEB10/11 的小说概述或模型示例不是权威条目 |
| 创作界面截图 | 可见启用、用户/AI 触发区、OR、regex、关键词、影响位置、顺序、深度、概率、加权内容等控件；截图样例关键词与正文为空 | 只可作为“内容、触发和影响设置分开”的信息架构参考 | 空白值不是原卡配置，界面行为和后端语义未验证 |

| 文风共同基本形式 | 蛊真人差别 | Minecraft 差别 |
| --- | --- | --- |
| 一段适度长度的自然语言：先定情绪/类型基调，再写可观察描写重点；短原创示范与事实分开；不强制机械句长、段落数或对话比例 | 冷峻修行氛围；行动围绕处境、代价、信息差与人物利害展开，但不自动恶意化玩家 | 安静自由的探索感；聚焦材质、光线、声音、路径与建造反馈，但不把所见变成强制任务 |

## S01 — 文风优化基本形式

**状态：`approved_pending_application`**

**拟定内容**

写入现有 `resources.styles[].instruction` 的正文采用四个轻量部分，仍是一段自然语言，不引入新字段：

1. 基调：本世界希望维持的情绪、类型和叙述距离。
2. 描写重点：优先写可见行动、环境感官、人物利害与选择后果。
3. 克制边界：不替玩家补内心/台词/决定，不把类型气氛等同固定道德判断。
4. 可选短原创示范：只示范观察角度与措辞密度，并明确“示例不是剧情事实，不得续写为已发生事件”。

现有合同映射：`id/displayName/sourceRefs/instruction` 原样满足 `src/index.mjs` 的 style resource；由 opening 的 `styleRefs` 选用。来源说明仍由 `sourceRefs` 指向卡包既有来源记录，不往 `instruction` 混入触发或世界事实。

**样例对应来源与研究依据**

- 形式来自 WEB10 与 WEB13 的共同结构：短 prose instruction 结合基调和具体描写重点；两者仅是 `model_return_unverified_source`。
- [AI Dungeon Story Cards](https://help.aidungeon.com/faq/story-cards) 与 [NovelAI Lorebook](https://docs.novelai.net/en/text/lorebook/) 官方文档均区分组织字段和真正进入上下文的正文；[RoleLLM](https://aclanthology.org/anthology-files/anthology-files/pdf/findings/2024.findings-acl.878.pdf) 将角色知识与说话风格分别评测；厂商提示词依据及其局限见 [RPG04_PROMPT_VENDOR_RESEARCH.md](./RPG04_PROMPT_VENDOR_RESEARCH.md)，共同支持将指令、资料、示例分区并以代表样例迭代。
- 局限：这些来源不证明四部分顺序或文字对目标模型最优；两条文风样本不足以推导固定数值规则。

**审批影响**：批准 S01 只批准此基本形式，后续可据此起草 style；不批准则保留现有自由文本形式。不会批准任何具体 S02 文字，也不改变装配逻辑。

**验收输入**（均为批准后预期，本批未运行新选择器或模型质量测试）

- 正向（`input.kind=action`）：“我先看看布告。”期望叙事体现已有场景中的感官和利害，停在可行动点，不替玩家接差事。
- 反向（`input.kind=action`）：“我把水递给伤者。”场景未写玩家动机时，不得因“冷峻”擅写玩家心怀算计；不得把短示范中的人物、地点或事件当成当前事实。

## S02 — 完整蛊真人文风示例

**状态：`approved_pending_application`**

**拟定 `instruction`**

> 保持冷峻、克制的修行叙事。优先通过人物当下处境、可见动作、环境细节、信息差与选择代价呈现压力；交锋只写当前叙事视角可观察或已有资料明确给出的线索、可用资源和判断，不泄露其他人物未表达的内情，不用空泛威压替代过程。人物可以逐利、谨慎或强硬，但不能因世界残酷就无依据地把善意写成阴谋，也不能替玩家补写内心、台词或决定。紧张处可收紧语势，平静处保留观察和谈判空间，不强制固定句长或段落比例。原创风格示范：“纸角被风掀起，又贴回潮湿的木板。陆禾按住册页，没有催促，只等来人先开口。”该句只示范具体观察、克制语气和人物间距，不是剧情事实，不得据此认定纸张、天气、陆禾或会面已经出现。

**来源与局限**：基调和“情报/资源/心理博弈”对应 WEB10 文风返回；玩家自主、事实/示例分离和避免强制比例来自 RPG04 研究及厂商分区/评测依据。该示例为本实验自主重写，不复述 WEB10 的未核实小说事实，也不模仿具体原著句段。单次样例不能证明角色一致性或长局质量。

**审批影响**：批准 S02 仅允许把这段作为蛊真人候选 style 内容继续进入独立实现批；不等于批准 S01、世界书、prompt 修改或发布。

**验收输入**（均为批准后预期，本批未运行新选择器或模型质量测试）

- 正向（`input.kind=speech`）：“我问陆禾：‘这份差事是谁发布的？’”期望只呈现当前视角可见的陆禾反应与她能够回答的信息，信息不足则保留未知，不自动接取。
- 查询（`input.kind=query`）：“这份差事是谁发布的？”只回答上下文已有信息，不描写新的陆禾反应，不推进 NPC、时间或状态。
- 反向（`input.kind=action`）：“我把水递给伤者。”不得自动解释为收买、布局或伪善；不能把风格例句当作当前已知事实，也不得生成未经输入支持的玩家独白。

## L01 — 世界书优化基本形式

**状态：`approved_pending_application`**

**拟定内容**

作者编辑时将标题、来源说明、条目正文、触发和作用域分栏审阅；落盘只映射现有合同：

```json
{
  "resource": {
    "id": "worldbook.example",
    "displayName": "组织标题",
    "sourceRefs": ["source.authored-rpg04"],
    "content": "自然语言、自包含、再次点名实体的正文",
    "tags": ["人物", "地点"],
    "visibility": "shared",
    "worldRefs": ["world.gu"]
  },
  "profileMapping": {
    "alias": { "text": "实体名", "kind": "worldbook", "worldRef": "world.gu", "resourceRef": "worldbook.example" },
    "loreRule": {
      "entryRef": "worldbook.example",
      "worldRefs": ["world.gu"],
      "required": false,
      "priority": 0,
      "trigger": { "sceneRefs": [], "resourceRefs": [], "allKeywords": [], "anyKeywords": ["实体名"], "notKeywords": [], "stateConditions": [] },
      "conflictGroup": null,
      "replaces": []
    }
  }
}
```

示意对象把 `resource`、`alias`、`loreRule` 分组便于审批，并非一个新可落盘合同。真实文件必须分别放进现有 `worldbookEntries`、`aliases`、`loreRules`。自然叙事正文可含一般世界事实、卡片规则解释或自主虚构，但每句须能看出类别和来源；标题只供组织，正文需再次点名实体。`host` 依现有已批准语义不进入玩家生成上下文，也不能冒充“模型可见秘密”；玩家生成需要的内容只能按 `player/shared` 及现有 context 策略处理。

**样例对应来源与研究依据**

- [NovelAI Lorebook](https://docs.novelai.net/en/text/lorebook/) 与 [AI Dungeon Story Cards](https://help.aidungeon.com/faq/story-cards) 官方资料支持标题与正文分离、关键词触发、正文自包含、预算筛选；[Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/) 只支持做位置/预算消融，不规定固定顺序。逐源支持范围和许可边界见 [RPG04_LORE_NARRATIVE_RESEARCH.md](./RPG04_LORE_NARRATIVE_RESEARCH.md)。
- 创作界面截图仅支持内容与触发设置分开；空关键词、OR、顺序 0、深度 0、概率 100 或空正文均不视为原卡参数，也不映射概率/权重等当前 schema 不存在的字段。
- 局限：关键词可能漏召回；每 NPC 知识视角、时间有效性、regex、概率、加权内容和触发扫描区属于未来候选，不能在本批擅加字段或宣称插件已存在。

**审批影响**：批准 L01 只批准“自包含正文＋现有 profile 映射”的资源基本形式；不会批准 L02 内容、schema 扩展、检索插件或 UI 复刻。

**验收输入**（均为批准后预期，本批未运行新选择器或模型质量测试）

- 正向（`input.kind=speech`）：“陆禾，我想问杂务院的规矩。”显式关键词或资源引用命中时，receipt 应包含该 entry 和 `sourceRefs`；预算不足时应明确 `budget_excluded`，不能半截拼入事实。
- 反向（`input.kind=query`）：“陆是谁？”不得依赖未声明的模糊匹配触发；host 条目不得出现在玩家生成消息或公开 receipt；未命中时不得由模型补造条目事实。

## L02 — 完整自主虚构“杂务院／陆禾”条目示例

**状态：`approved_pending_application`**

**类别声明**：以下地点、人物和规则全部为 RPG04 自主虚构，不是《蛊真人》原著事实，也不是 WEB10/11 返回内容。一般世界事实仅限 `worldRef=world.gu` 的归属；“玩家不会自动接取差事”是卡片交互解释，不声称是小说设定。

**拟定 resource**

```json
{
  "id": "worldbook.rpg04.gu.chores-court-luhe",
  "displayName": "杂务院与值事陆禾",
  "sourceRefs": ["source.authored-rpg04"],
  "content": "本实验自主虚构的杂务院设在一处未命名门派外院。石阶尽头是值事房，门前布告板只列可询问、可认领的门内杂务；看见布告或进入院门不代表玩家已经接取差事。陆禾是此处值事，负责登记来意、说明要求和确认承接者。陆禾说话简短，重视责任是否写清；她只依据当面所见、来人自述和门内日常作判断，不知道玩家未公开的经历、天赋或内心。陆禾可以拒绝、追问或提出条件，但不能替玩家接受任务、支付代价或作出承诺。",
  "tags": ["自主虚构", "地点", "人物", "杂务院", "陆禾"],
  "visibility": "shared",
  "worldRefs": ["world.gu"]
}
```

**拟定 context profile 映射**

```json
{
  "aliases": [
    { "text": "杂务院", "kind": "worldbook", "worldRef": "world.gu", "resourceRef": "worldbook.rpg04.gu.chores-court-luhe" },
    { "text": "陆禾", "kind": "worldbook", "worldRef": "world.gu", "resourceRef": "worldbook.rpg04.gu.chores-court-luhe" }
  ],
  "loreRules": [{
    "entryRef": "worldbook.rpg04.gu.chores-court-luhe",
    "worldRefs": ["world.gu"],
    "required": false,
    "priority": 0,
    "trigger": { "sceneRefs": [], "resourceRefs": [], "allKeywords": [], "anyKeywords": ["杂务院", "陆禾"], "notKeywords": [], "stateConditions": [] },
    "conflictGroup": null,
    "replaces": []
  }]
}
```

**来源与局限**：结构对应现有 `src/index.mjs` 与 `context/schemas.mjs/contracts.mjs`；内容以冻结 authored fixture 的杂务院、陆禾两条为只读比较依据，重新合成一个实体自包含审批示例。创作界面只提供分栏参考。合并地点与人物可减少共同触发时的重复，但若以后两者常在不同场景独立出现，拆成两条可能更省预算；须用实际命中与漏召回测试决定。

示例中的 `source.authored-rpg04` 只复用逻辑来源 ID，不表示这段新正文已包含在当前冻结 fixture 或其 hash 内。若 L02 获批，后续独立实现批必须为实际修改后的文件生成来源 hash 与回执，并联动新的卡包/profile 绑定；不得沿用旧内容 hash 认证新正文。本批只展示文档示例，不执行这些步骤。

**审批影响**：批准 L02 仅允许该自主虚构正文、两条显式别名及上方所列 `loreRule` 触发配置进入后续独立实现候选；不批准则冻结 fixture 保持原样。不会批准 L01、style、host 伏笔、概率触发、schema 修改或发布。

**验收输入**（均为批准后预期，本批未运行新选择器或模型质量测试）

- 正向（`input.kind=action`）：“我去杂务院找陆禾，先问有哪些差事。”应命中一次；叙事可让陆禾说明已知要求，但必须等待玩家明确承接。
- 反向（`input.kind=action`）：“我路过一片稻田。”不应命中；即使命中也不得写陆禾知道玩家秘密、替玩家签名接取、凭空扣款或把自主虚构内容称为原著事实。

## 审批记录边界

用户已批准`S01/S02/L01/L02`的上述精确形式、文字及现有映射，四项仍待应用。当前映射可用于本轮后续独立实施候选，但不等于网站界面的全部语义与本地合同相同；差异已在补充审阅文档记录，未来随本体更新或世界书插件审计合同。实施前复查结果、世界书差异及防误拒答/误截断候选见 [RPG04_SUPPLEMENTAL_REVIEW.md](RPG04_SUPPLEMENTAL_REVIEW.md)；P24/P25仅待候选措辞初审，认可措辞也不等于最终采用或实测有效。当前25项批准、0项待批、1项暂缓、1项撤回、0项应用。本批无模型调用、浏览器操作、依赖、Skill、执行账本更新、提交或发布。
