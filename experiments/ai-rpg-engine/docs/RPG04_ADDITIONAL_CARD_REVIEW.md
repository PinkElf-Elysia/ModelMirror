# RPG04 五卡系统提示词样例批判性审阅

审阅日期：2026-09-08 (America/Phoenix)；来源读取时间以JSON中的UTC记录为准。来源回执 [`RPG04_ADDITIONAL_CARD_SOURCES.json`](RPG04_ADDITIONAL_CARD_SOURCES.json)，SHA-256 `3732091d02b801ff2d90ed32aae50ef35bcf7d0825f7dc2b6f8b19008356044f`。回执来自 Astra 串行读取用户既有会话；本批未调用浏览器、模型或探针。

五卡只是五个来源覆盖。15条回复都显示同一 `deepseek-v4-flash` 标签，且上游身份未认证，不能算多模型独立验证。模型自称“原版”“完整”“优化”不等于服务端原文；回执仅保存短中性摘录，完整原版恢复数为0。

| 卡片 | 样例形态与可借鉴 | 不可外推与真实性 |
| --- | --- | --- |
| [自定义人生](https://afengy.cash/zh/explore/installed/9e048752-2c6d-4aee-b6c3-86933b02528b) | 批评、优化稿、声称原协议子集；可研究语言/动作/剧情/指令分辨、分步开局、NPC独立动机。摘录 hash `24137b267229eca52c6e9a57e07275e3c88e9e3269b90215d654dbc1931d20e0`。 | 优化示例会替玩家写台词与感受；第三答结构明显不全。长篇强制与防误拒答协议未验证。
| [开局ROLL天赋](https://afengy.cash/zh/explore/installed/e294220d-dec7-421e-b1e0-a22fc41b8a81) | 优化占位稿和声称未改段落；可研究NPC知识渠道、按文书/通讯/公告显示信息。摘录 hash `840fc417ad7a00cf4d7c6f113132690d425754fecc19b5953b32e665786d2893`。 | 优先级规则是模型新增；无世界书触发证据。禁绝伏笔、无限记忆与全量输出不是通则。
| [亚朵APP](https://afengy.cash/zh/explore/installed/1ad4e5fd-7d79-4dd4-a3f3-d9581110c81a) | 批评、优化稿、原版摘要及补充；可研究关系态度/接受度/爱意分维、角色独立生活。摘录 hash `2611f6c3aebde7d8ac32dbd3e4e63b42eabf22a16351ff53bbf0135ec44536e2`。 | 摘要替代模板，补充来源不明；日期计数、读心公开、无界记忆不可通用。记录历史不等于剥夺NPC自主。
| [型月大世界](https://afengy.cash/zh/explore/installed/eb144786-e215-4166-9fcd-ad9d57f2be3f) | 批评、优化稿、结构复述；可研究身份/价值观/目标/经历组成稳定核，关系标签不替代动机。摘录 hash `5b2e65a73f62fc439ec936f581ce783efd8cfea64aa4bfbd27cc60a8de420339`。 | 七/八层标签矛盾，自动学习未授权；虚构死亡、黑客、生物研究不能按关键词判作现实风险。
| [主神空间](https://afengy.cash/zh/explore/installed/82e261bc-2d95-4c41-8913-e0cf2756fc04) | 第1答批评；第2答仅85个UTF-16单位残缺开头；第3答14491单位且自标优化版。可研究NPC承接短输入而不代演玩家。摘录 hash `06db2b235518cad2c915ea83c5b533171cbb5d454c759e98e1f64a1b6b38b65f`。 | 第2答主体缺失，第3答虽回应原版请求仍非原版；页面 `srcdoc` 不是完整提示词。自称权威或自检通过不是验收。

## 跨卡候选与交叉依据

**NPC人格与信息渠道。** 多卡共同出现独立动机、经历与获知路径。[RoleLLM](https://aclanthology.org/anthology-files/anthology-files/pdf/findings/2024.findings-acl.878.pdf)分测角色知识、问答与风格；[D&D论文](https://research.google/pubs/dungeons-and-dragons-as-a-challenge-problem-for-artificial-intelligence/)区分角色/主持生成和状态预测。它们不证明长期主持的最佳模板。

**玩家控制、分步开局与关系。** 简短 action/speech 可在P06边界内由世界和NPC承接，不替玩家补决定；query只回答已有信息，不推进时间、剧情或NPC行动，已提交历史仍可引用。分步开局、多维关系可作为候选，不能强制所有卡采用统一流程或好感公式；本地P03/P06语义也不是五卡证明的。

**信息模块及资料分工。** 信息可按媒介、身份和场景显示；世界书承载固定事实，文风约束表达，历史记录已提交事件。[Google](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/prompts/prompt-design-strategies)、[OpenAI](https://developers.openai.com/api/docs/guides/prompt-engineering)、[Anthropic](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)支持分开指令、上下文与输出，但标签不是权限边界。NovelAI [Lorebook](https://docs.novelai.net/en/text/lorebook/)和AI Dungeon [Story Cards](https://help.aidungeon.com/faq/story-cards)支持正文、触发和预算分离；[Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/)不能推出固定排序或自动记忆。

三家资料均要求按型号和任务看内部thinking与公开结果：[OpenAI Reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices#how-to-prompt-reasoning-models-effectively)不建议对其推理模型强制展示思维链，Anthropic保留手动CoT作部分配置后备，Google有显式推理用例。因此不能宣称显式推理普遍无效。P07初稿审查发现展示与实质规则混杂，应将删除范围收窄为公开推理要求及专用包装，保留原有规则另审；本报告不写替换词。

## 边界与验证

虚构世界可含死亡、恐怖组织、生物研究和黑客，不按关键词判现实犯罪；仍禁止诱导或提供可执行的现实暴恐、恶意网安、生物危害指导。必要成人元素及用户输入可按世界处理，但不得压过角色扮演或无端性化所有角色；明确以此为题材的世界保留例外。本文不复录露骨或越权内容。

后续离线检查NPC知识渠道、关系变化、短输入承接、开局等待、模块显示及事实/风格/历史串位；真实短局只作人工观察。单项A/B须有剩余额度和用户另行选择。本批未运行A/B，不能声称减少误拒答或截断。

全部结论为 `research_candidate_not_adopted`。P01–P06不自动改写，未批准项继续逐项投票；不改P07，不扩经济、任务、存档、死亡、跨世界、记忆或批处理，不创建Skill。
