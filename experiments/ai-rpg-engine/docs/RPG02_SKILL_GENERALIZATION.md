# RPG-02 Skill 两世界扩展验证

2026-09-05，状态 `verified_pending_manual_review`，`claimAllowed=false`。用户追加授权两例完整世界资源提取及 Skill 自我优化；本记录不替代 RPG-02 人工验收，也不启动 RPG-03。

## 已交付

| 实际来源 | 世界描述/代表人物 | 身份及物资 | 天赋全部字段 | 总记录数 |
|---|---:|---:|---:|---:|
| 470.蛊真人 (Reverend Insanity) | 1 | 5 | 30 | 36 |
| 383.赛博朋克2077 | 1 | 5 | 15 | 21 |
| 合计 | 2 | 10 | 45 | 57 |

第二例选赛博朋克2077，区别于原先蛊真人/Minecraft 的代表取样。每份结果完整保存所选世界对象的 `name/desc/boss/identities/talents`、身份 `name/items`、天赋 `name/color/cost/desc/type`；字段内容、数组顺序、显示名和身份物资全部保留。蛊真人与原样本重叠的七条记录逐字段相同，包含中洲门派的外门弟子。

“全量”只覆盖该次正常开场中观察到的完整世界对象。通用天赋池为独立作用域，未整体划入任一世界；用户的 root 天赋仍保留在原代表 fixture 中。本次未取得隐藏世界书、文风和提示词，没有补造这些来源。死亡重生、价格/颜色等保持来源数据，不成为运行规则或真实权限。

原文证据可持久恢复：

- [蛊真人 capture](../fixtures/skill-generalization/gu-world.capture.json)：原对象 7504 UTF-8 字节，SHA-256 `9f99336567d3cb331a25e82679d4a9783b25b16d3a475aae07bb8840aefa952b`。
- [赛博朋克 capture](../fixtures/skill-generalization/cyberpunk-world.capture.json)：原对象 4293 字节，SHA-256 `17df5d126145a2993b5667c9547f77049465f8a64c72bab51fcdba615d8e30a4`。
- [蛊真人完整结果](../.rpg02-work/skill-generalization/delivery-20260905/gu-world.first.json)：10656 字节，SHA-256 `04fc26753def4f8db052731f6d8b92f8e398f21acc9e9e90085d06e6a745e67c`。
- [赛博朋克完整结果](../.rpg02-work/skill-generalization/delivery-20260905/cyberpunk-world.first.json)：6605 字节，SHA-256 `8872f60fc5e8f58a05fd5a31120423e666cac073bb231053662c66a3c8f87d3b`。

结果位于 Git 忽略的隔离交付目录，丢失时可从两个受版本管理的 capture 离线重建。[机器回执](RPG02_SKILL_GENERALIZATION.json) 保存输入、工具和 Skill 的 hash、独立运行证据及完整性边界。

## 初测失败与改进

先让 Sol 仅根据旧 Skill 和两个新输入独立尝试。旧 Skill 只说明固定 `fixtures/rpg02/*` 的卡包编译/ZIP 重放；直接提供全世界对象时，旧提取器缺 `worldDB/commonTalents` 声明而返回 `TARGET_MISSING`，旧编译入口返回 `SOURCE_CAPTURE_POLICY`，失败后没有产出文件。旧选择 Schema 还固定每世界至多两个身份、八项天赋。这些结果说明原配方尚未泛化；原 96 项测试通过不能代替新例验证。

本次在同一隔离模块补齐：

1. **独立源资源提取入口。** `tooling/world-source.mjs` 将完整 world-capture 校验后转为 world-extraction JSON，遍历全部身份/天赋；没有放宽原编译 Schema、改写旧黄金输入或重置测试基线。
2. **严格静态字面量解析。** 复用 Acorn 白名单，必须只有一个完整表达式，拒绝执行结构、重复属性和歧义；重新 hash 的恶意表达式也不能绕过。
3. **有界浏览器取样。** 两个精确世界候选各只读 8192 UTF-16 单元窗口，识别完整对象后静态校验，再从新 DOM 精确复读范围。两个对象为 4176/2378 UTF-16 单元，复读相同。完整 1762427 单元开场没有传回或保存；相比原先 56 块整页读取减少了此次传输材料，未做速度基准，也未资格化任意大窗口。
4. **可重复输出与失败停批。** 严格 Schema、来源/字节/hash 核对、2 MiB 限额、无覆盖新文件写入、回建逐字节验证；同名、负价格和 root 文本不触发去重、经济或授权语义。
5. **Skill 明确分流。** 新完整世界源证据流程与原代表卡包流程分开，记录输入、输出、作用域、未取得内容及停批条件。见 [Skill](../skills/rpg02-selected-content/SKILL.md) 与 [浏览器步骤](../skills/rpg02-selected-content/references/browser-capture.md)。

## 再次独立执行与主审复核

Sol 第二次仅接收更新 Skill、两个 capture 和互斥输出目录，不读取测试预期、不操作浏览器。每个输入各执行 extract/verify 两次，八条真实 CLI 全部 exit 0、valid:true；分别得到 36/21 项记录。两次结果逐字节相同，字段覆盖完整，`losses=[]`、`runtimePermissions=[]`。完整 forward receipt 的必要内容已嵌入机器回执，原本地文件位于 `.rpg02-work/skill-generalization/forward-sol-20260905/run-01/forward-receipt.json`。

Astra 在独立交付目录再次执行八条 CLI，比较实际文件字节与 Sol 输出，两个世界均一致。新增测试 14/14 通过，覆盖真实全量、原样本重叠、缺失/未知字段、重复显示名、负价格、AST 攻击、重新 hash、深度/体积限制、UTF-8、输入不变性、确定性、拒绝覆盖和 Windows 链接/路径。原 RPG-02 门禁仍为 96/96；Skill 官方原验证脚本通过。

```powershell
node --test --test-reporter=spec tests/world-source.test.mjs
npm.cmd run verify:rpg02 -- --base a43cfa389e1785a95f04a006ba26550a5a36965e
git diff --check
```

Node 24.18.0 / npm 11.16.0 与依赖锁未改变；新增测试不并入旧固定测试数。G0 两个捕获文件、G1 五个实现/Skill/测试文件、G2 五个回执/研究文档文件，每批通过门禁再前进。主审在 G1 内补齐 CLI 直接入口、精确 flags、Windows 输出名、文档总量与 Schema 版本，未跳过失败项。G2 最终聚合曾因 MANIFEST 的 CRLF 被 diff 检查拒绝；在同批内统一为 LF 后重跑同一门禁。

## 权威边界与交接

浏览器来源仍由 Astra 串行确认；独立 Sol forward run 验证的是已捕获输入的离线提取，不能据此声称无人值守浏览器已合格。仅两个世界验证了当前字面量/字段模式；页面漂移、未闭合窗口、新字段、登录、弹窗、限流、额度或 hash 分歧时停批。Luna 未启动，后续更多世界/量产仍需另行授权。

world-extraction 是来源材料，不包含稳定资源 ID、卡包开局绑定或新增玩家配置，不能直接冒充可运行卡片。原代表卡包仍为 14 条真实来源记录、27 个编译资源、两开局，RPG-01 合同和 RPG-02 /content 接口、黄金记录不变。模型/记忆由模镜治理、可选功能进插件市场、内容进卡片市场的方向不变。

本次网站消息探针和外部模型调用为 0，账本未修改。固定 HEAD 为 `a43cfa389e1785a95f04a006ba26550a5a36965e`，分支 `codex/ai-rpg-rpg02-content`，只改两个白名单目录。主检出区 HEAD 与原 78 条状态不变；没有 Commit、Push、PR、Merge、Deploy、Release、Publish。回退只需在人工确认本次回执列出的路径后撤销本次扩展；原代表流程与受保护内容无需迁移，不提供宽泛清理命令。
