# RPG-02 验收与交接记录

固定基线 `a43cfa389e1785a95f04a006ba26550a5a36965e`，分支 `codex/ai-rpg-rpg02-content`。PR #359 合并由前期只读核实，本任务没有执行合并。RPG-01 两目录与验收 HEAD `dd207b4bff123538d922d571b8cfb7425020ef8c` 的匹配证据见 [RPG02_BASELINE.json](RPG02_BASELINE.json)。

本轮交付状态为 **implemented_pending_manual_acceptance**，**claimAllowed=false**。这表示离线内容交付待用户验收，不能代表游戏运行、叙事质量、全量浏览器执行或后续轮次已经完成；本轮追加的 Terra 小批资格已通过，见文末。最终自动检查以实际命令成功标记和 [机器状态](RPG02_STATUS.json) 为准。

## 已验证的内容

- 正常卡片开场取得蛊真人、Minecraft、四个身份及八项天赋；完整保留用户五项天赋。14 项真实记录来自 18 个字面量片段，共 2801 UTF-8 字节。最小派生 HTML 载体为 3822 字节。
- 来源回执保存原文字面量、定位、字节数和 SHA-256；一次新 DOM 复读确认片段一致。完整原 HTML 仅瞬时用于定位，未保存。其历史 DOM hash 不充当服务器原文件 hash。
- 编译后共 27 个资源：14 项真实记录、4 项派生身份物资、9 项自主补写背景/文风/世界书/开场/信息模块。补写是最小转换示例；没有宣称取得原卡未显示的内容或完成叙事质量优化。
- 完整章节玩家文本可绑定到蛊界外门弟子；固有背景、当前身份、身份等级、人物战力、天赋等级和拥有/激活分别表达。黄金配置显式启用五项天赋，权限数组始终为空。
- 同名不等于同 ID；别名按种类和世界作用域解析。原始价格、等级标签、颜色等只保留为来源数据，未建立经济/死亡/结算等核心规则。
- 内容层纯函数；工具层才处理文件、hash、归档和 CLI。来源脚本不执行，失败稳定诊断不包含原文、绝对路径或堆栈。

## 批次边界与纠错证据

P0、02A1、02A2、02B1、02B2-R、02B2、02C1、02C2、02D1、02D2、02E 已依序通过各自门禁。各语义子批按最多五个版本控制文件推进；生成物在忽略目录，不计入版本控制交付。

| 批次 | 核心证据与本批内修正 |
|---|---|
| P0/02A1/02A2 | 固定基线、12 份初始研究文件 hash、主区状态、冻结合同；安装只使用已批准精确依赖与 npm lock |
| 02B1/02B2 | 合成 AST 正反例和真实片段双证据；历史导出/剪贴板/URL/EPERM 失败未冒充成功，按最新提取授权完成选定证据 |
| 02C1 | 主审发现同名世界可能误绑天赋；改用已登记 worldScope，歧义/漂移/同名回归通过后进入下一批 |
| 02C2 | 校正通用与世界天赋标签、重复别名绑定和多物资包歧义；保留用户原章节全文，未使用研究 Markdown 代替原始格式 |
| 02D1 | 检查实际解压量、CRC、hash、UTF-8、封套 Schema、来源引用及链接；提前执行字节限额，失败回收只限本次输出 |
| 02D2 | 主审加入原文字面量后附语句、重新 hash 的攻击样例；要求 AST 仅一项表达式，来源漂移与真实 CLI 全链回读通过 |
| 02E | 六个冻结输入、七个成员和两次独立重放与实际 CLI hash 匹配；未修改 quick_validate.py 配合真实 PyYAML 校验 Skill 成功 |
| 02F1 | 模块说明、机器状态、验收记录、聚合验证器与固定验证辅助，共五个文件；以本轮完整门禁验收 |
| 02F2 | 研究 README、路线、审计、计划及 MANIFEST，共五个文件；最终文档清单独立记录收束结果 |

Skill 校验曾遇到本机 Python 缺 PyYAML。临时 shim 的结果未作为权威门禁；最终使用现有真实 PyYAML 环境运行未修改的检查器，主审以禁用 pycache 的方式重跑，输出 `Skill is valid!`、退出 0。该一次性文档检查环境不成为模块运行、安装或交付依赖。

## 权威自动门禁

在模块目录，使用 Node 24.18.0 与 npm 11.16.0：

```powershell
npm.cmd ci
npm.cmd run test:boundary:rpg02
npm.cmd run test:contracts
npm.cmd run test:content
npm.cmd run test:archive
npm.cmd run verify:rpg02 -- --base a43cfa389e1785a95f04a006ba26550a5a36965e
git diff --check
git status --short
```

| 测试组 | 通过数 | 重点 |
|---|---:|---|
| 原合同 | 28 | 冻结接口、严格结构、权限、查询与插件声明 |
| RPG-02 边界 | 7 | 两目录范围、内容/工具分层、源执行/网络/子进程、链接 |
| 内容 | 43 | 静态提取、真实来源、作用域/别名、玩家、漂移、离线 CLI、黄金重放 |
| 归档 | 18 | 未声明项、路径、链接、压缩/实际字节限额、CRC/hash/UTF-8/Schema、回滚与可复现性 |
| 合计 | 96 | 无跳过、失败、取消或 todo |

聚合还核实固定基线/分支/祖先关系并记录 candidateHead、冻结文件 hash、精确依赖及 11 个直接/传递包许可证和 integrity、安装脚本禁用、13 份现行研究文件 hash/字节数、历史探针账本及待人工验收标志，最终输出 `RPG02_AUTOMATED_GATES_OK`。该代码范围检查是工程护栏，不宣称操作系统网络沙箱或任意恶意 JavaScript 的完整静态证明。

F1 主审重新执行 npm ci（11 个锁定包）和完整聚合，退出码 0，得到上述 96 项成功标记。聚合初稿的状态名、成功标记与 lock 键序比较问题均已在本批修正；另外核对了固定版本、清单精确集合、安装脚本禁用和漂移时先停止测试的行为。

旧 RPG-01 聚合脚本、常量和 28 项原合同测试原样保留；不在新基线上将旧聚合失败伪装通过。父仓全量前后端测试未运行，也不是本轮门禁。

## 实际 CLI 与黄金产物

被 Git 忽略的 `.rpg02-work/delivery-rpg02-20260905/` 保留 `card/`、`representative.zip`、`replay/`。实际 CLI compile → pack → verify → unpack 均退出 0。七个成员包含卡包、完整玩家、索引、回执、两个来源文本及清单。

- ZIP：32827 字节，SHA-256 `b9e4948208e91813732fe1cf8f50db2b20ee41dc05ff7bb990467b3e5d7f2b41`。
- 清单：SHA-256 `b1638d24d41bfc29afbf0a29d089c85fc05b0051c8ef11ff91d77aaeb6032cd0`。
- 各输入/成员完整 hash 见 [RPG02_GOLDEN.json](RPG02_GOLDEN.json)，来源分类及历史失败见 [source-capture.json](../fixtures/rpg02/source-capture.json)。

相同输入和转换器版本保证固定序列化/排序/时间戳/level 0 ZIP 的可复现性；离线回放不证明站点当前未变化。ZIP 为受限自有封套；ZIP64、额外字段、数据描述符、注释、自解压、多卷等不受支持。TXT 来源按不可信 UTF-8 保存，JSON 来源要求固定信封；不进行媒体或任意文件导入。

## 后继消费与保留边界

RPG-03 消费已校验卡包和玩家配置；RPG-04 消费补写文风、世界书可见性及来源索引；RPG-05 消费配置结果与稳定诊断。它们必须独立规划与授权，不能从本轮推断模型接入、运行权限、检索或 UI 已存在。

原代表资源阶段由 Astra 负责首次交互、异常与最终验收；Sol 固化 Skill，该阶段尚未启用 Luna。后续追加试跑及现已通过的小批资格见下节。后续批量操作须独立授权、黄金小批通过、互斥分片和独立页面状态，漂移即停批交回 Astra。

主检出区 HEAD `2434257fa9db630ac7b247f73010457f94192f8f` 与同一 Git 参数下原有 78 条状态逐项一致。本任务仅在两个白名单目录变更，未修改父仓 client/server、RAG、记忆、插件、矩阵绿洲、Docker 或 CI。历史账本未改，本轮网站消息探针与外部模型调用均为 0。

该自动验收记录在发布前封存，`publication:false` 是该阶段历史快照；用户后来已明确授权资格通过后的 Commit、Push、PR，实际动作以 Git/PR 元数据为准。未授权或执行 Merge、Deploy、Release、Publish，未进入 RPG-03。人工验收不由测试结果替代。回退为停止消费该独立实验交付，保留本地证据，不涉及共享服务、数据库或父仓文件回滚。

## 用户追加的低级 worker 量产门槛（小批已通过）

用户先将低级 worker 可执行后续常规全量提取列为必要门槛，后明确授权“资格收尾后提交 PR”。当前为 **qualified_small_batch**、`finalAcceptanceReady=true`；合格型号为 `gpt-5.6-terra`，Luna 未取得最终配方资格。原离线交付仍为 `implemented_pending_manual_acceptance`、`claimAllowed=false`，叙事质量和后继轮次未据此验收。

已固化只读捕获适配器、固定路径/排他锁、来源和双输出、文件检查点、事件 hash 链、回执，以及由工具生成的无损转交文本和唯一 `String.raw` 包装。相关 39 项通过（世界 14、适配器 17、批处理 8），加原 96 项为 135 项。普通文件 symlink 分支因本机 EPERM 未实测，directory junction 有真实拒绝验证。

Luna 的历史流程/传输失败保留；Terra 在旧转交方式下完成两世界跨上下文续跑，但另一原神项目失败。更新后的最终配方由新 Terra 完成原神后，在另一新上下文创建标签时遇到 CUA 内核退出并按规则停批。主审确认原文/双输出及停止事件完整，F 分片未 finalized。这些恢复前的失败和未通过结论完整保留，不用部分成功抵销。

完整尝试、回执 hash、停止事件及报告纠正见 [worker 门槛](RPG02_WORKER_GATE.md) 和 [机器记录](RPG02_WORKER_GATE.json)。恢复后新建 H（原神、蛊真人）与 G（赛博朋克2077）互斥分片，由三个全新 Terra 上下文完成；H2 仅凭磁盘 assignment/status 接续 H1 的 pending，全部 16 条 CLI exit 0、valid=true，无失败或重试。主审独立复读三个真实 DOM 对象，逐字段核对 3 世界、15 身份/物资、75 天赋，共 93 项；raw/data hash、原文、完整数组、空权限、零损失和双输出一致。H/G finalize/audit 均通过，完整回执与事件链已固化。

本次汇总检查曾因 AST null-prototype 与普通 JSON 对象原型不同误报，写入前停止；改为完整 JSON 值比较后原门槛重跑通过。文档写入也曾因模块/仓库相对路径混用在写入前停止，按已登记路径修正。原工具、Skill、来源和预期 hash 均未改。

收尾修复仅涉及 RPG-02 聚合验证器及既有边界测试：合法后继提交不再触发 exact-HEAD 误报，固定 base、祖先、分支、冻结合同和所有原校验保留，输出同时记录 base/candidateHead。Sol 首次在只读沙箱跑聚合遇到测试临时目录 EPERM，提升权限原命令重跑 96/96；提交后主审仍须运行同一门禁。

本轮只取得对应型号和冻结结构的小批资格，不执行全量、不进入 RPG-03。全量提取固定放在 RPG-06 第一版路线完成后，再独立授权批次、检查环境/资源类别与黄金小批。用户已授权本轮通过门禁后的 Commit、Push、PR；Merge、Deploy、Release、Publish 均未授权。

## 暂存与最终字节资格复验

暂存检查发现黄金 JSON 多余末行，以及边界测试和 worker CLI 各一处混合换行。修正仅改变空白，Acorn token/JSON 值比较一致；仍记录原/新字节 hash 并暂停资格，以新的 I/J 分片重新完成三个全新 Terra 上下文、三世界及 pending 续跑。16 条 CLI 全部成功、无重试，主审重新核对真实 DOM、完整字段、93 项资源、双输出和回执。当前 worker CLI SHA-256 为 `19160f9ce97c5bf2b5d9b35073138edd77f5bf2fedf1d1b9f1c7d0240ae149b6`；Skill 与适配器未改。旧 H/G 成功和所有失败证据均保留，最终资格只绑定 I/J 的提交字节版本。

原 96 项和扩展 39 项在空白修正后重跑通过；`git diff --cached --check` 通过。I/J finalize/audit 均 exit 0。最终机器状态恢复 qualified_small_batch / finalAcceptanceReady=true，保留 pending manual acceptance。发布前所有受管理文档 hash 再次核对，提交后执行同一聚合门禁。
