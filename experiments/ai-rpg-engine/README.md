# AI RPG 合同与离线内容编译

私有 ESM 包 `@modelmirror/ai-rpg-contracts@0.2.0`。RPG-02 交付两个代表世界的可核对资源、玩家文本解析、开局组合、离线编译、受限 ZIP 与转换回执。原离线交付为 `implemented_pending_manual_acceptance`、`claimAllowed=false`。用户追加的低级 worker 小批资格已通过，合格型号为 Terra，`finalAcceptanceReady=true`；用户已授权收尾后提交 PR。人工验收与后继实施授权分别处理。

## 接口与兼容

根入口保持 RPG-01 原样：四种精确 `0.1.0` 合同，以及 `validateCardPackage`、`validatePlayerSetup`、`validateTurnExchange`、`evaluatePluginReadiness`。详见 [冻结合同](docs/CONTRACTS.md)。

新增 `@modelmirror/ai-rpg-contracts/content`：

```js
extractSourceRecords(htmlText, selection)
parsePlayerText(text)
compileContent(input)
```

这三个同步接口返回 `{ valid, diagnostics, value? }`，成功时才有值。诊断按稳定顺序提供阶段、级别、代码、JSON Pointer 和必要关联指针；不回显原文、绝对路径或堆栈，不修改输入。内容层不读写文件、调用网络或模型。

- 提取器使用 parse5 和 Acorn，仅解释明确目标变量的字面量 AST，不运行来源脚本；拒绝调用、成员求值、插值、展开、getter、计算属性、重复声明和歧义。
- 玩家解析仅接受 [原样章节文本](fixtures/rpg02/player-text.txt) 所示格式，返回待绑定草稿。保留完整人物描述、XP、背景、当前身份及五项天赋；“携带”只表示拥有。
- 编译器通过显式 ID 映射和“种类＋世界作用域”别名绑定；零匹配、多匹配、悬空或来源不一致均失败。该版本针对代表资源配置，不能当作全量或通用 Tavern 转换器。
- 纯编译回执为 `hashVerification=tooling_required`。工具层 `compileVerifiedContent` 实际校验载体、片段、选择、玩家文本与补写内容的 hash 后，才产生 `verified_selected_evidence` 回执。

辅助 Schema 均为 `0.1.0`：source-selection、compile-input、content-index、conversion-receipt、bundle-manifest；JSON 来源成员另用 source-document 信封。它们不改变 RPG-01 资源字段。

## 代表材料

| 类别 | 交付 | 来源边界 |
|---|---|---|
| 世界 | 蛊真人、Minecraft | 真实交互提取 |
| 身份 | 每世界两项，共四项；含中洲门派外门弟子 | 原文与物资保留；等级为显式配置，回执有提示 |
| 天赋 | 玩家五项＋Minecraft 三项，共八项 | 真实提取，等级/价格/颜色保持来源含义 |
| 物资 | 四个身份物资包 | 从所选身份物资文字派生，只授予所选身份对应物资 |
| 背景、文风、世界书、开场、信息模块 | 2＋2＋2＋2＋1 项 | 最小自主编写示例，标记 authored，未做叙事质量验收 |

共 **14 项真实记录、18 个原文字面量片段、27 个编译资源、两个开局组合**。黄金玩家选择蛊界外门弟子；[player-config.json](fixtures/rpg02/player-config.json) 明确逐项配置五项激活，激活不是从原文推断。E 级身份、SSS/UR 天赋、人物战力和固有龙人公主背景分开；`runtimePermissions=[]`，虚构 root 没有真实权限。

[来源回执](fixtures/rpg02/source-capture.json) 保存选定片段、定位、字节数及 SHA-256。[selected-source.txt](fixtures/rpg02/selected-source.txt) 是自行构建的最小 HTML 数据载体，标记 derived；真实记录可按用户授权原样复用。完整原 HTML 未落盘。整页 DOM hash 只是一项历史观察，不能冒充服务器原文件 hash，也不能证明网站现在未变。

## 工具与本地交付

使用 Node **24.18.0**、npm **11.16.0**。精确依赖和 11 个直接/传递包的许可证、完整性见 [登记](docs/RPG02_THIRD_PARTY.json)。安装可访问 npm 注册表，生命周期脚本由模块 `.npmrc` 禁用；内容工具运行不需要网络。模块本身不提供操作系统沙箱。

```powershell
npm.cmd ci
New-Item -ItemType Directory -Force .rpg02-work | Out-Null
node tooling/cli.mjs compile --input fixtures/rpg02/compile-input.json --html fixtures/rpg02/selected-source.txt --selection fixtures/rpg02/source-selection.json --capture fixtures/rpg02/source-capture.json --player-text fixtures/rpg02/player-text.txt --player-config fixtures/rpg02/player-config.json --out .rpg02-work/example-card
node tooling/cli.mjs pack --input .rpg02-work/example-card --out .rpg02-work/example-card.zip
node tooling/cli.mjs verify --input .rpg02-work/example-card.zip
node tooling/cli.mjs unpack --input .rpg02-work/example-card.zip --out .rpg02-work/example-replay
```

所有输出路径必须全新，父目录已存在。文件工具拒绝覆盖，校验全部通过后才创建输出；写入失败只回收本次创建的材料。输入文本、文件和归档均有限额。省略两个 player 参数可生成不含玩家的包，不能只省略其中一个。

实际已通过 CLI 编译、打包、校验、解包的审阅材料位于被 Git 忽略的 `.rpg02-work/delivery-rpg02-20260905/`。黄金成员 hash 与两次独立重放结果见 [RPG02_GOLDEN.json](docs/RPG02_GOLDEN.json)。[可复用 Skill](skills/rpg02-selected-content/SKILL.md) 记录已验证的离线流程和后续取样停批边界；本轮没有 Luna 量产。

## 归档边界

只接受 bundle-manifest.json、card-package.json、可选 player-setup.json、content-index.json、conversion-receipt.json 及清单声明的 sources/&lt;sourceId&gt;.txt 或 .json。TXT 是不可信 UTF-8 文本，JSON 必须符合 source-document Schema。

限制为 64 个文件、单文件 2 MiB、总解压量与 ZIP 输入各 16 MiB、单项压缩比最多 100；HTML 输入最多 16 MiB。逐项核对实际字节、CRC、SHA-256、UTF-8、Schema 和跨文件引用，目录与 ZIP 共用校验器。

ZIP 接受 stored/deflate；拒绝未声明项、穿越、大小写重复、链接、Windows 特殊路径、加密、嵌套归档及不支持方法。为固定封套格式，还拒绝 ZIP64、注释、extra fields、data descriptor、多卷、自解压前缀和附加尾字节。导出固定排序、规范 JSON、1980-01-01 时间戳及 level 0 存储方式，并强制回读比较。相同输入和转换器版本产生相同归档 hash。

## 验证与交接

```powershell
npm.cmd run test:boundary:rpg02
npm.cmd run test:contracts
npm.cmd run test:content
npm.cmd run test:archive
npm.cmd run verify:rpg02 -- --base a43cfa389e1785a95f04a006ba26550a5a36965e
git diff --check
```

四组测试为 7＋28＋43＋18，共 96 项。聚合门禁另外检查固定基线、冻结文件、两目录范围、工具链、依赖/许可证、研究 MANIFEST、账本和待验收状态。旧 verify:rpg01 保留旧基线历史用途；父仓全量前后端测试不属于本轮权威门禁。见 [验收记录](docs/RPG02_ACCEPTANCE.md) 与 [机器状态](docs/RPG02_STATUS.json)。

RPG-03 消费已验证卡包/玩家配置，RPG-04 消费文风/世界书/来源索引，RPG-05 消费配置结果/必要诊断。尚未实现运行时、模镜接入、提示词编排、检索、UI、插件加载、市场、媒体或通用 Tavern 导入。模型、记忆和权限继续归模镜控制面；所有可选增量走插件市场，内容走卡片市场，卡片特化经济/死亡/继承语义保持数据。

父仓客户端、服务端、RAG、记忆、现有插件、矩阵绿洲、Docker、CI 没有本任务变更。本轮网站消息探针与外部模型调用均为 0。本说明封存于发布前，用户已授权资格通过后的 Commit、Push、PR；实际动作以 Git/PR 元数据为准，未授权 Merge、Deploy、Release 或 Publish。回退只需停止使用这个独立 worktree 和其未跟踪交付，不涉及主服务、父仓数据或迁移；保留源证据供人工复核。

## 追加的 worker 门槛

已固化 [提取 Skill](skills/rpg02-selected-content/SKILL.md)、只读捕获和固定分片/回执工具；原 96 项和新增 39 项检查通过。最终冻结配方由三个全新 Terra 上下文完成两个互斥分片、三个世界及 pending 续跑，16 条 CLI 全部成功，93 条源资源零损失。资格为 `qualified_small_batch`，只授予 Terra；Luna 尚未通过最终配方。实际成功、失败、保留的停止事件和恢复边界见 [门槛报告](docs/RPG02_WORKER_GATE.md) 与 [机器记录](docs/RPG02_WORKER_GATE.json)。全量、其他资源类别及 RPG-03 均未启动。实际全量提取安排在 RPG-06 第一版路线完成后，需另行批量授权与对应黄金小批；本轮不建设调度器。

最终发布字节说明：暂存检查发现黄金 JSON 多余末行，以及边界测试和 worker CLI 各一处混合换行。修正仅改变空白，Acorn token/JSON 值比较一致；仍记录原/新字节 hash 并暂停资格，以新的 I/J 分片重新完成三个全新 Terra 上下文、三世界及 pending 续跑。16 条 CLI 全部成功、无重试，主审重新核对真实 DOM、完整字段、93 项资源、双输出和回执。当前 worker CLI SHA-256 为 `19160f9ce97c5bf2b5d9b35073138edd77f5bf2fedf1d1b9f1c7d0240ae149b6`；Skill 与适配器未改。旧 H/G 成功和所有失败证据均保留，最终资格只绑定 I/J 的提交字节版本。 详见 worker 门槛机器记录的 `packagingNormalization` 与 `finalQualification`。
