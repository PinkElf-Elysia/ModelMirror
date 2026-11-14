# 单角色卡首版

新增由 AI 扮演的单角色卡，与世界卡并列。支持创建或导入、编辑草稿、封存版本、选择开场白、独立会话、恢复和导出。实现位于 `experiments/ai-rpg-engine/character/`，复用作品存储和受控模型宿主。

PR 组合基线为 `a6563a9b8e761e3190d0ac09cfd373886802516b`。原始开发基线 `d9dd7094` 的历史验收在本地保留，不作为组合代码的兼容承诺。角色素材、私有会话和本地验收产物不随代码分发；仓库测试只使用中性原创夹具。首张正式卡已在本地完成用户验收，本仓库不提供其定义或验收数据。

用户操作见[创建单角色卡](../../client/src/content/help-center/articles/create-single-character-card.md)；主会话接入见[Harness 接口交接](CHARACTER_HARNESS_HANDOFF.md)。

## 独立规范与兼容矩阵

实现仅依据独立格式规范，不复制、移植或翻译改写 GPL/AGPL 解析器、UI、运行逻辑或测试。未新增生产依赖。

- [V2 / 8083fb388615ccbce768e97cbbd49d2b3214632c](https://github.com/malfoyslastname/character-card-spec-v2/blob/8083fb388615ccbce768e97cbbd49d2b3214632c/spec_v2.md)
- [V3 / f3a86af019fbd99f788f7a1155f399655b34ab35](https://github.com/kwaroran/character-card-spec-v3/blob/f3a86af019fbd99f788f7a1155f399655b34ab35/SPEC_V3.md)

CCardLib、OpenRouterArchived/character 只用于核对模块边界与来源，不引入其实现。

| 项目 | 已实现 | 限制 |
| --- | --- | --- |
| V2 JSON | 校验、导入、核心编辑、同版本导出、单角色会话 | 不代表酒馆运行行为全兼容 |
| V2 PNG | 常见 tEXt/chara，独立 CRC/结构/长度/字段校验，导入与导出 | 非 tEXt 元数据封装不支持；不执行图片附带代码 |
| V3 JSON/PNG | ccv3 识别、核心映射、nickname 替换、未知字段保留、同版本 JSON 导出、原文件下载 | 不提供编辑后 V3 PNG 导出 |
| V3 资源及其他元数据 | 引用、群聊开场、多语言作者说明、日期等保留 | 不拉取远程资源，不运行群聊或资源行为 |
| V3 → V2 | 独立损失清单，明确选择后导出 | 不覆盖原始材料；不支持行为的书条目禁用 |
| 角色书 | 关键词、大小写、常驻、次级关键词、深度、顺序、优先级、位置、递归与预算 | 默认深度 4 条消息、预算 1024 估算 token；深度最多 200，递归最多 32 轮；正则/装饰器条目跳过 |
| 扩展 | 未修改字段、嵌套扩展保留并再次导出 | 不安装插件，不执行脚本，不授予权限 |
| CHARX/ZIP、复杂资源包、动画、完整 V3 装饰器 | 明确反馈；动画原图保留 | 无 CHARX 导入；肖像仅静态默认画面 |

JSON 和解码后数据上限 16 MiB；PNG 上限 32 MiB；PNG 画布上限 4000 万像素。HTTP 请求考虑 base64 开销，角色路由上限 45 MiB。沿用宿主 JSON 深度 64 / 50 万节点、重复键及危险对象键校验；角色书最多 2000 条。最终作者文档仍受已有 16 MiB 文档上限约束。肖像资产沿用 5 MiB 限制，原始 PNG 独立保留。

同时有有效 V3/V2 时选择 V3；V3 无效时不静默降级，显示候选诊断后允许明确选 V2。同版本不同元数据冲突拒绝。普通 PNG 返回“未发现角色卡数据”，可单独上传为肖像。

## 三层合同

1. **交换材料**：原文件按 SHA-256 保存。文件名、来源、选择版本与导入兼容报告是独立不可变收据，不写入标准卡。编辑不改原文件。
2. **模板与版本**：复用 AuthoringStore 的 workId、revision、versionId、CAS、操作幂等与不可变 configuration。角色分支为 `format: modelmirror.rpg.character-document/1, kind: character`，包含完整 `character.card` 与原始材料引用。旧文档不加类型字段、不做批量迁移。
3. **会话实例**：保存精确版本引用及配置哈希、展开后的开场文本/索引、玩家 ID 引用与称呼快照。真实回合仍是完整玩家/角色对；开场独立为 assistant 初始消息。记忆、设置、请求意图/回执/哈希均以会话 ID 隔离。

`character/types.ts` 是这些边界的公开类型；运行时仍以校验器为准。角色名、作者版本字符串不能代替内部 ID。
预留 `worldInstanceRef: null`，不代表读写世界状态的能力。未来主会话只通过固定版本和实例引用接入，不能把世界结算、记忆、凭据或授权塞回标准卡。

## 上下文和宿主接入

- `context.mjs` 为独立可测试的确定性装配器。描述、性格、场景持续参与装配；作者说明、标签、作者和作者版本不进入指令。
- 支持不区分大小写的 `{{char}}`、`{{user}}`、`<BOT>`、`<USER>`。两项提示词中的 `{{original}}` 分别指向各自默认值；历史后提示默认值为空。其他宏原文保留并诊断。
- `system_prompt` 替换角色层默认提示词，宿主权限规则始终保留；`post_history_instructions` 位于真实历史及当前输入之后。没有世界卡前后置词混用。
- 开场创建时一次性冻结，零模型调用；不作为玩家回合，也不在刷新/每轮重新插入候选列表。示例独立标注，容量不足先裁剪，不写入真实历史或记忆。
- 角色书扫描当前输入和选定历史，不扫描作者说明/示例。输出预览包含触发、禁用、未命中、不支持与预算排除原因；估算 token 不等于模型真实 tokenizer。
- 会话宿主复用现有 SessionRecords、JointBudget、ControlledProvider、模型控制与 memory-palace 校验/召回内核。内建私有记忆需每会话明确启用；本轮未启用自动付费记忆抽取/总结，没有世界卡插件或授权继承。
- 请求发出前冻结最终 messages、模型选择和哈希，持久化预算占用。未知结果阻止新发送，恢复仅查询同一请求，不重新发起生成。
- 角色 API 不接收浏览器完整提示词、模型网关地址或工具权限。标准卡不能扩大宿主能力。
- 默认离线夹具通过 SSE 提供分段事件。已有受控 Provider 在完整回执后返回正文，真实通道当前为 **receipt-buffered**，页面明确说明；没有将模拟分段声称为真实逐 token 输出。
- 真实通道只能由可信宿主通过 `startAuthoringServer({characterSettings:{provider,control,limit}})` 注入已有 ControlledProvider / ControlPlaneModelControl。额度默认 0，不继承其他会话额度。离线测试不证明人物长期稳定性或真实记忆效果；真实调用需另行授权。

## HTTP 和共享改动

现有作品 create/save/publish/read/copy API 接受角色文档分支，存储格式和事务逻辑未另造一套。

新增前缀 `/rpg-app/api/authoring/characters`：

| 接口 | 用途 |
| --- | --- |
| GET blank / works / status / models | 空白文档、本地角色、运行模式、现有模型目录 |
| POST imports/inspect | 只检查材料，不创建作品 |
| POST imports | 带检查哈希和 operationId 确认导入 |
| POST portrait / preview / export | 肖像、确定性装配预览、独立交换导出 |
| GET source?work=&version= | 下载未改动原始文件 |
| POST sessions | 仅版本引用、开场索引、玩家身份及 operationId |
| GET sessions/:id / sessions/:id/events | 恢复快照与 SSE |
| POST sessions/:id/send / recover | 创建请求或查询原请求 |
| POST sessions/:id/settings / memory | 会话 CAS 设置及私有记忆 |

`/characters` 是公开的静态编辑器外壳，可作为顶层导航打开；数据读取和写入继续通过现有 Host、Origin、防跨站检查。没有放开跨站草稿/会话 API。

共享差异集中在：

- `authoring/contracts.mjs`：角色文档校验/封存/能力声明分支。
- `authoring/store.mjs`：复制角色时同步角色名与作品显示名，保留原材料及扩展；旧复制语义不变。
- `authoring/library.mjs`：可选 kind、角色简介和标签。
- `authoring/server.mjs`、`browser.mjs`：最小路由、注册与角色入口重定向。
- `runtime-binding.mjs`、`summary-session.mjs`、`joint-session.mjs`：精确基线指纹兼容函数接入；旧世界运行入口明确拒绝角色文档，要求走角色宿主。
- `client/src/pages/rpgLibrary.ts`、`RpgPage.tsx`：可选角色类型和筛选，缺省仍是世界卡。
- 两个隔离源码测试补复制新依赖，保留原有漂移拒绝断言；故事馆测试新增类型筛选。

没有修改交易规则、商店适配、世界状态结算、主 /api/chat 或并行工作树。

## 受控网关消息合同

可信角色宿主在 story 请求上声明 `messageContract: "modelmirror.rpg.character-messages/1"`。网关仅在 authoring-task/1、/2 中接受此合同，校验以下序列：

```text
system, assistant(固定开场), user, (assistant, user)*, [system(历史后指令)]
```

每条消息只含 role/content，数量及类型受限；内部 system、tool、未知合同及非 story 使用会拒绝。标记参与 sourceRequestHash，但不转发给供应商。未声明合同的世界、总结、记忆及比较请求沿用原校验；鉴权、额度、参数、幂等和未知结果语义不变。

跨语言回归 `server/tests/test_rpg_character_contract.py` 通过真实 JS CharacterSessionHost / ControlledProvider 到 Python ASGI / MockTransport 检查原文、序列、重启和隔离。它是禁网的合同验证，不调用真实模型。

## 旧存档与指纹

`legacy-transition.json` 固定记录最新主线 a6563a9b 的 before 哈希、当前六项共享实现的 after 哈希及新增语义依赖的哈希。仅在所有约束匹配时规范化旧世界指纹，不改存档绑定、不接受任意旧值。最新主线已有插件升级兼容规则保持原样。

`baseline-fixture.mjs` 从该 Git 提交导出源代码，每次生成中性世界及存档，再由当前代码恢复并续聊。Offline、Summary、Joint 都必须保留原 binding/executionHash，且拒绝篡改的 runtimeHash。测试无需本地旧素材；浅克隆需要先取得基线 Git 对象。输出只写忽略目录 `.rpg04-work/character-baseline/`。

兼容清单只涵盖本次验证的精确基线。未来共享实现发生变化时，应重新审核语义及未变组件并重跑旧存档验证，不能机械更新哈希或关闭校验。历史开发中的角色会话不承诺跨源码升级；保留其原宿主和证据，不重写旧绑定。

## 离线验证与独立预览

在仓库根目录运行：

```powershell
node --test experiments/ai-rpg-engine/character/*.test.mjs experiments/ai-rpg-engine/authoring/runtime-binding.test.mjs experiments/ai-rpg-engine/authoring/library.test.mjs experiments/ai-rpg-engine/authoring/store.test.mjs
python -m pytest server/tests/test_rpg_character_contract.py -q
npm.cmd --prefix client run test -- --run src/pages/RpgLibrary.test.tsx src/pages/RpgAuthoringEntry.test.tsx src/content/help-center/helpContent.test.ts
npm.cmd --prefix client run build
npm.cmd --prefix experiments/ai-rpg-engine/card-replica run build
```

Python 跨语言用例需要 Node 和 engine 的既有 npm 依赖。受控回执落盘验证使用 Linux 环境；Windows 上曾出现回执落盘 pending 的环境差异，不宣称 Windows 受控网关路径已经验证。

从引擎目录启动隔离的本地夹具预览（选择空闲端口）：

```powershell
node authoring/server.mjs --creator --directory .rpg04-work/character-local --port 60449
```

打开 `http://127.0.0.1:60449/characters`。默认回复来自确定性测试夹具，不调用模型。配置父前端的 `VITE_RPG_AUTHORING_PORT` 指向同一端口后，可从故事馆进入。

## 限制与回退

不包含多角色、自动抽取 NPC、离场演化、共享世界记忆、商店交易、角色广场、自动记忆抽取或完整酒馆行为兼容。受控输出目前为回执后显示，不能视为真实逐 token 流式。全量后端测试和生产部署须按发布任务另行验收。

回退时先停止本次独立预览，保留其数据及对应源码，然后撤销角色注册和本次代码。不能用旧宿主强开角色存档、删除材料或改写绑定；本次无需数据库迁移，也不修改共享数据。
