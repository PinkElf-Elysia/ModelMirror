# 单角色卡 → RPG Harness 接口交接

## 可接入的能力与边界

本分支在主线 `a6563a9b` 上提供单角色模板、版本和独立会话。主线 `harness/` 的商店原型仍拥有世界状态与确定性交易结算；本分支没有改变其实现，也没有把角色接入世界实例。以下现有接口可复用；世界关联是后续工作，不能把预留字段当作已经实现的能力。

| 层次 | 现有实现 | 所有权与约束 |
| --- | --- | --- |
| 原材料 | `character/sources.mjs`、`exchange.mjs` | 原文件按哈希保留，导入报告独立；不运行扩展或拉取资源 |
| 草稿、版本 | `authoring/store.mjs`、`contracts.mjs`、`character/document.mjs` | 沿用 workId / revision / versionId、CAS 与操作幂等；名字及作者版本不是 ID |
| 上下文 | `character/context.mjs` | 确定性装配，提供 trace；开场、示例与真实历史分别处理 |
| 角色实例 | `character/session.mjs` | 精确固定版本及配置哈希；memoryScope 等于 sessionId，禁止默认跨会话召回 |
| 受控执行 | `authoring/controlled-receipt.mjs`、后端 `rpg_authoring_execution.py` | 宿主控制模型、额度、权限与请求恢复；角色文本不能改变权限 |
| 展示入口 | `authoring/library.mjs`、`client/src/pages/rpgLibrary.ts` | 可选 kind=character；缺省继续按世界卡处理 |

运行类型见 `character/types.ts`，最终以运行时校验为准。

## 版本与会话合同

通过已有作品 API 创建、保存、封存。角色文档带 `format: modelmirror.rpg.character-document/1`、`kind: character` 和完整 `character.card`。不要将角色文档传给 Offline/Summary/Joint 世界宿主，它们明确拒绝该分支。

角色 HTTP 根路径是 `/rpg-app/api/authoring/characters`。创建会话只接受：

```text
POST /sessions
{ operationId, reference: { workId, versionId }, openingIndex, player: { id: null | string, name } }
```

`openingIndex=0` 选择主开场，后续索引选择候选开场。宿主保存展开后的 openingText、玩家称呼快照及 configurationHash；开场不产生模型调用，不占真实玩家回合。草稿更新或封存新版都不会回写旧会话。

`GET /sessions/:id` 恢复快照；`POST /sessions/:id/send` 使用 operationId 与 expectedRevision；`recover` 只核对原请求。结果未知时保留请求意图、预算占用与回执证据，不自动补发或换模型。设置和私有记忆使用各自 API 及会话修订检查；不要写存储文件或复制另一实例的 memories、operations、授权与预算。

`GET /sessions/:id/events` 是角色宿主自己的 SSE。受控 Provider 当前在完整回执后返回正文，不能宣称逐 token 流式；离线夹具的分段事件不代表供应商行为。

## 提示词映射与网关

`assembleCharacter({card, player, opening, history, input, memory, maxTokens})` 返回 messages 与 trace。描述、性格、场景持续进入上下文；作者说明、作者、标签和作者版本排除。示例只处于独立示例区，不能写入历史或记忆；预算不足时优先裁剪示例。角色书只按已声明的关键词等规则触发，未知行为保留并报告。

`system_prompt` 替换角色层默认词，宿主规则始终保留；`post_history_instructions` 位于本轮输入之后。不要映射成世界卡的前后置词。支持 `{{char}}`、`{{user}}`、`<BOT>`、`<USER>` 及两项提示词各自的 `{{original}}`；其他宏仅保留并提示。

角色请求必须带 `messageContract: modelmirror.rpg.character-messages/1`。网关只对 story 任务允许 `system, assistant(opening), user, (assistant,user)*, [system(post)]`；标记参与请求哈希但不发送到供应商。不能给世界请求批量添加标记，也不能放宽全局消息校验。

## 未来世界关联的接入约定

当前绑定中的 `worldInstanceRef` 固定为 null，没有世界查询、写入或同步接口。后续若需要关联，应在单独任务中定义实例关联及显式授权，不改写已封存版本和既有绑定。

- 世界宿主继续拥有账本、交易、状态更新和事件事实；角色输出仅作为叙事候选，不能直接提交结算。
- 世界上下文进入角色前必须按可见性、角色知识范围和 token 预算裁剪，记录来源及世界修订；自由文本“知道某事”不等于读取权限。
- 私有会话记忆不可默认上送世界或分享给另一角色；如要形成世界事件，定义单独的验证与采纳流程。
- 关联、状态变化及角色请求应各自保留幂等标识与失败状态；未知生成结果不等于交易失败，不重放既成结算。
- 不自动抽取世界卡 NPC、不共享插件授权、不将私有聊天和运行权限导出到标准卡。

这些是后续接入需遵守的边界，不是本分支已实现的通用 Harness。

## 合并时的冲突检查

共享变化局限于作品合同/存储、目录、工作台注册、世界宿主类型拒绝与精确指纹适配、角色消息网关校验。跨这些层的变更必须一起审查，不能只移植 UI 或只放宽网关。

最新主线已有的 `previousHashes` 插件升级规则保留。`character/legacy-transition.json` 只适用于 a6563a9b 的精确文件组合；若主会话继续改共享文件，先检查语义差异及未变组件，再生成基线中性存档并验证 Offline/Summary/Joint 恢复、续聊和篡改拒绝。禁止通配接受旧哈希、修改旧绑定或仅更新清单数字。

回归入口见 [单角色卡首版](CHARACTER_CARDS_V1.md)。原 d9dd7094 历史证据、正式卡原稿/修订、失败请求及后续验收均保留在本地，不能跟随 cherry-pick、提交或测试夹具入库。未来部署与真实调用需要独立授权及预算。
