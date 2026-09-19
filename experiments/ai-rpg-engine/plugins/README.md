# RPG 可选插件：本轮竖切已通过，候选已收尾

用户已逐份通过首轮、原路线续轮与分支续轮。本轮完成可选市场→安装→会话授权→正文分支→新对话续玩闭环；停用/卸载/重装及保留数据由已确认的离线与UI批次覆盖。实际Provider共3次，剩余1次保留，不再自动派发；这不代表长期内容质量或发布门禁通过。

[验收结论与证据](./B5-REVIEW.md) · [独立授权账本](./B5-AUTHORIZATION.json) · [当前状态](./STATUS.json) · [交付hash](./DELIVERY.json) · [操作与回退](./OPERATIONS.md)。审阅入口 http://127.0.0.1:18437/rpg/earth 保留，两路线均为2回合；正式功能离线入口18433、帮助重放18435及全部数据保留。

当前分支codex/rpg-plugin-branch-save，基线bac37a6e5691feeda36d1ec9a548d0692652ad59。源码与已确认B4一致；本次只收尾文档，158项旧交付hash、三份Provider原文、父会话/分支快照和账本复核通过。126项离线/mock和生产构建等为此前实际运行证据，本步无代码改动不重复运行。

尚未Commit/Push/PR或共享部署。本轮不继承旧发布授权。未来发布前需刷新/整合主线并复验；3项无关帮助图片历史失败继续保留。回退优先停用插件，保留当前核心读取、会话、节点和账本，不直接降级宿主。以下为不改写的阶段历史。

# RPG 可选插件：B5 分支续玩待审阅

首轮与原路线续轮均已获用户“通过”。第3次分支续玩完整返回，原路线和分支各2回合，共享首轮并各自续玩；原路线文件与分支点快照未变。实际分支请求仅包含首轮历史，未带入原路线第2回合；浏览器原文hash与Provider保存一致。

当前已用3/剩余1，暂停派发，第4次保留。审阅 http://127.0.0.1:18437/rpg/earth 中“龙婉翩 · 另一条路线”第2回合；完整原文位于 .rpg04-work/plugin-b5/data/dispatches/earth/slot-3/response.txt。[授权账本](./B5-AUTHORIZATION.json) · [完整核验记录](./B5-REVIEW.md)。当前审阅代理只读，正式核心聊天未改。

Gemini 3.8 Flash／Google AI Studio、temperature=0.7/top_p=0.8/max_tokens=16384保持不变，世界书默认关闭。本次上游报告费用$0.05307675，非独立账单审计。第三份人工审阅尚未完成，未宣告初版闭环通过。未改核心代码、提交、发布或更新共享栈。以下为保留的阶段历史。

# RPG 可选插件：B5 原路线第2回合待审阅

首份真实输出已获用户“通过”。已从第1回合创建“龙婉翩 · 另一条路线”，并完成原路线第2回合；分支仍为1回合，快照未改变。当前已用2/剩余2，暂停等待第2份输出人工审阅，分支续玩尚未运行。

[授权账本](./B5-AUTHORIZATION.json) · [请求、输出与核验记录](./B5-REVIEW.md)。审阅入口：http://127.0.0.1:18437/rpg/earth → 龙婉翩第2回合；完整原文在 .rpg04-work/plugin-b5/data/dispatches/earth/slot-2/response.txt。可在普通对话列表切换分支，切换不调用模型。此验收入口仍为只读代理。

本次实际发送完整历史，本轮前后置词各一次；Gemini 3.8 Flash/Google AI Studio、0.7/0.8/16384及世界书策略不变。浏览器原文hash与保存文件一致，上游报告本次费用$0.05189625，非独立账单审计。未修改核心代码，未追加真实派发或发布。以下为保留的阶段历史。

# RPG 可选插件：B5 第一份真实输出待审阅

用户已确认B4，并为本轮地球 OL 分支竖切新增4次正式派发额度。现已完成第一份首轮输出，已用1、剩余3，暂停等待用户审阅。没有把剩余额度批量消耗，也没有宣告真实分支衔接或内容质量通过。

[本轮授权账本](./B5-AUTHORIZATION.json) · [完整技术与人工审阅记录](./B5-REVIEW.md)

审阅入口：http://127.0.0.1:18437/rpg/earth → 历史 → 龙婉翩。真实原文在独立忽略目录`.rpg04-work/plugin-b5/data/dispatches/earth/slot-1/response.txt`。网页已打开；“查看原文”的hash与Provider原文件一致。该审阅代理仅允许读取，正式产品的额度内连续游玩实现未改。已有18433/18435入口与数据保留。

实际模型Gemini 3.8 Flash／Google AI Studio，经既有OpenRouter受控适配器；temperature=0.7、top_p=0.8、max_tokens=16384。首轮只有system与角色资料+玩家输入，派发前后hash核验一致。有效作者文本、参数、模板与世界书策略没有修改。上游报告费用$0.045477，未独立审计账单。

本轮分支点及两路线续轮尚未运行；须收到对首份输出的人工审阅后继续，每份输出单独审阅。旧RPG05派发0，旧额度/账本不变；新账本是明确新授权而非重置旧预算。无自动重试、Commit/Push/PR或共享栈更新。

B4-DELIVERY.json原字节保留。以下历史中“真实0/待授权”等是当时状态，由上方当前记录覆盖，不回写旧证据。

# RPG 可选插件：B4 封装候选

B0 UI v2、B1、B2、B3均已获用户确认。B4已补齐帮助、操作合同、候选镜像和离线验证，等待本批确认。全站帮助图片检查仍有3个已证实的无关历史残留；本次RPG图检查通过。真实分支续玩与人工内容审阅尚未运行，不能宣告初版闭环通过。

## 交付入口与文件

- 原功能验收入口保留：http://127.0.0.1:18433/rpg 。
- 新帮助重放入口：http://127.0.0.1:18435/help/branch-rpg-story ，使用全新虚构数据、Provider禁用。两个入口均不连接共享后端；重启前必须检查当前PID与命令行。
- 用户帮助：client/src/content/help-center/articles/play-rpg-cards.md、branch-rpg-story.md；现有帮助模板承载内容，没有新增UI流程。
- 插件生命周期/存储/版本/故障/回退合同：[OPERATIONS.md](./OPERATIONS.md)。当前状态与源码、构建hash：[STATUS.json](./STATUS.json)、[DELIVERY.json](./DELIVERY.json)。B1/B2/B3旧登记保留原字节。
- 详细验收与失败历史：docs/help-center/evidence/rpg-branch-b4-bac37a6e.md。两份用户PNG均来自真实浏览器截图，无损重编码且解码像素一致，源JPEG保留；旧RPG入口图移入历史证据，不删除内容。

## 本批验证结果

- 通过：帮助/RPG/受影响Studio前端63项；插件/Studio/原地球卡63项，共126项离线/mock。父前端TypeScript与生产构建通过，既有大chunk警告保留。
- 通过：独立候选镜像modelmirror-rpg:plugin-b4，ID sha256:63eed9fb3c259ca97dd6ba959cfbd3e5ef18d33cdd0f1c25c6c3cdfe9fa3c739；两卡前端在镜像中构建通过。16份镜像关键源码/产物及运行hash与本地一致。收尾文档写于镜像构建后，镜像内旧文档不作为当前交付状态。
- 通过：无网络/只读根目录/新tmpfs/预算0容器内HTTP验证：安装不启用、分支幂等、卸载后宿主重启、分支读取/离线续玩、父路线和快照保持、重装不恢复授权。无用户卷，无端口映射，真实派发0。
- 通过：全新18435按8步教程可见重放，草稿恢复、默认不授权、停用保留数据符合文案；帮助图实际加载1000×800，390px帮助页面clientWidth=scrollWidth=380。
- 失败（已证实基线遗留）：全站verify:help-images从5处降为3处，剩余b266b870/studio-beta-headings.jpg、eeb5bbd2/creator-cache.jpg、eeb5bbd2/studio-layout.jpg为无关模块未引用资产。未删除或隐藏，未降低检查标准。当前新增RPG截图引用/PNG/尺寸/体积/alt/基线均通过。
- 未运行：B5真实Provider、真实分支历史衔接/人工输出验收、共享升级、CI、Commit/Push/PR。没有新增或继承调用额度。

刷新origin/main=a29da8a5，比当前接受基线bac37a6e领先2提交；包含App.tsx文件交集。本批保持已确认候选，不混入其他功能。后续获PR授权后必须整合最新基线并复验，不能将此候选称为最新共享部署。

## 停止点与回退

本批等待用户确认；B5还需明确新额度，逐份输出交人工审阅。PR/共享部署另行授权。优先停用插件，保留当前核心及会话、分支、快照、原文和账本；旧宿主不能直接读取新分支目录，不做未经验证的降级。没有改共享栈，无需恢复共享状态。撤回B4只恢复本批帮助/打包文件并停止核对归属后的自有预览，保留全部数据和证据。

## 以下为 B3 及更早历史记录

下列待验收、失败与当时的未运行状态按原样保留，以各批确认记录及上方B4当前状态为准。

# RPG 可选插件：B3 市场与聊天候选

B0 UI v2、B1、B2 均已获用户确认。B3 现已完成本地实现、离线/mock与浏览器验证，等待本批人工确认；B4完整帮助/候选镜像封装与B5真实Provider/人工叙事审阅尚未进行。不能据此宣告初版闭环完成。

## 当前使用路径

隔离候选入口：http://127.0.0.1:18433/rpg 。该入口使用本批父前端生产构建，代理独立18432 RPG服务；Provider禁用，不连接共享后端、用户会话或真实账本。预览数据在独立模块忽略目录 `.rpg04-work/plugin-b3-preview/data/`。当前兼容的虚构会话为「林遥（当前候选离线验收）」及「林遥的新路线」；早期源码字节版本的验收会话仍保留可读，不追认兼容。

1. RPG首页 → 插件市场 `/rpg/plugins` → 安装「分支存档」。安装仅登记审核制品，不自动启用。
2. 打开地球 OL 会话 → 顶部「插件」 → 阅读权限并明确启用。
3. 在任一已完成回合正文下点击「分支」 → 输入新对话名 →「创建并进入」。后台保存不可变快照，不派发模型。
4. 新路线作为普通会话出现在原「对话列表」，显示父对话和分支回合。每个会话的未发送草稿分别保留，切换后恢复。
5. 新路线插件默认关闭，可直接聊天；如要继续分支，需再次明确启用。停用/卸载保留已有会话和节点，重装不恢复旧授权。

地球 OL 不再因已有一轮输出锁定发送；玩家主动发送仍受服务器原预算、完整运行版本与未确认请求门禁约束。切换/刷新不自动派发或重试。发送前将请求ID与输入持久化，结果未知保留同一ID、要求恢复核对；分支未知结果使用原操作ID恢复。浏览器存储异常时提示保留内容并阻止发送/切换。插件失败提示独立于核心聊天。

原始作者文本、角色模板、世界书策略、模型配置及参数保持原样。首轮为完整system及角色资料+用户输入；续轮为同一system、完整原始历史和本轮官方前置词→输入→官方后置词。分支名称、快照信息、工程回执不进入模型消息。RPG05继续半成品归档，暂不兼容此插件。

## B3 实际验证

```powershell
# 仓库根
node --test experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/*.test.mjs
node --test experiments/ai-rpg-engine/card-replica/tests/*.test.mjs
# client/
npx.cmd vitest run src/pages/RpgPluginsPage.test.tsx src/pages/RpgChatState.test.ts src/components/StudioBetaPanels.test.tsx --configLoader runner --maxWorkers=1 --fileParallelism=false
npm.cmd run build
# card-replica/
npm.cmd run build -- --base=/rpg-app/earth/
```

- 插件/Studio 37、地球卡26、受影响前端10：共73项通过，全部离线/mock。宿主HTTP→假Provider实测4次：原路线首/续轮、分支续轮、原路线再次续轮；逐字比较历史/装配，验证共用额度及耗尽后不派发。
- 两处TypeScript/生产构建通过。父前端既有大chunk警告保留，没有调高阈值或关闭检查。
- 真实浏览器验证：安装与启用分离、正文分支/浮层/原列表、父子草稿隔离与刷新、取消不入历史、停用、卸载/重装不恢复授权、保留分支离线续玩。Enter/Esc及空名称禁用通过；390px聊天与市场无横向溢出。网络未知结果/落盘故障主要由状态和HTTP测试覆盖，未声称所有故障均经浏览器注入。
- 实际截图位于 `docs/ai-rpg-experiment/plugin-branch-prototype/b3-*.jpg`，均为正式浏览器截图原字节，按返回hash归档。`b3-current-create.jpg`为最终构建；create/list/mobile为同批早期构建，随后只修正取消按钮显示及源码换行，交互设计未改；market为最终市场构建。
- 初次状态测试因导入路径错误失败，修正后同一套10项通过。最后按钮修正脚本首次因工作目录路径错误未写文件，纠正后完成并重跑地球构建、10项前端测试和浏览器分支/取消。保留这些失败，不计作首次成功。
- 无真实Provider派发、无新预算、无共享栈更新、无Commit/Push/PR。当前源码/截图/构建hash见 DELIVERY.json；B1/B2历史登记分别为B1-DELIVERY.json和B2-DELIVERY.json。B2镜像不是B3交付镜像，B3镜像封装待B4。

## B3 范围与回退

父仓变化仅RPG首页、市场页、App路由及相关测试。Studio布局、Matrix Oasis、旧RPG05、公共回合合同、作者资源、provider/models和依赖锁未改。聊天拆为平台入口、会话UI、客户端状态，存储和生命周期仍在独立RPG服务。

优先在会话中停用插件，保留当前核心读取能力及全部会话/分支/节点/账本。若撤回B3前端，只恢复本批前端文件，不删除B2数据。旧宿主不能枚举新分支目录，降级部署前仍须另行验证读取兼容。预览是本批独占实例；任何停止前核对PID与命令行，不关闭共享栈或旧卡入口。

## 以下为 B2 及更早历史记录

以下状态是对应批次当时的记录，由上方B3当前状态覆盖；不把历史待验收或失败回写成提前通过。

# RPG 可选插件：B2 存档与分支候选

B1 已由用户回复“确认”。B2 已实现并完成离线/mock验证，等待本批人工确认；正式市场/聊天 UI 与连续输入解锁属于 B3，真实 Provider 与人工叙事审阅属于另行授权的 B5。已批准 v2 原型保持不变。

## B2 使用与 HTTP 接口

先安装 `rpg.branch-save`，在新建地球 OL 会话显式启用，完成至少一轮对话。向 `/rpg-app/earth/api/sessions/:id/branches` POST：

```json
{"operationId":"unique-new-operation","expectedSessionRevision":1,"turn":1,"name":"向北走"}
```

首次成功返回201：`{session, snapshotHash, replayed:false}`；重复完全相同操作返回200及同一分支当前状态。同一父会话下同一 operationId 改变内容返回冲突。请求不接受 system、模型、运行版本、授权或预算字段。该操作保存所选完整回合的后台快照并创建普通会话，符合批准后的“正文分支按钮 → 命名浮层 → 原对话列表”流程，不再要求玩家先进入独立节点界面。

- 普通 `/earth/api/sessions` 列表包含分支的 `parentId` 和 `branchTurn`；GET会话提供原有安全展示，以及版本兼容状态。切换只读不生成。
- GET `/earth/api/sessions/:id/plugins/rpg.branch-save/nodes` 返回本会话已创建分支对应的后台节点名称、回合和snapshotHash，要求插件仍明确启用。卸载后普通会话读取不依赖此接口。
- 新分支 requests 为空、插件未启用；新请求不能复用继承历史中的请求ID。再次分支须在该分支显式启用。
- 创建分支不调用模型。预算不足仍可创建分支。所有路线继续使用原服务 `dispatches/earth/` 账本；分支、重装和新会话没有增额或重置路径。

## B2 运行版本及旧会话

`studio/runtime-binding.mjs` 由服务器绑定生效 system、前后置词和世界书原文hash、装配器/卡片源码、底层存储/新存储源码、模型与Provider配置源码及配置内容。角色/世界/完整参数/模式另有setupHash，均随会话和节点保存。实际发送仍由原装配器执行，世界书默认关闭，首轮/续轮顺序及模型参数不改。

前端不能指定版本。配置被篡改、运行版本不匹配或启动后绑定文件发生变化时拒绝发送/分支；读取原文不要求当前版本匹配。不下载旧运行代码、不静默迁移或换模型。

旧会话没有 runtime 字段时返回 `RUNTIME_UNVERIFIED_LEGACY`，保持原文可读，但本候选拒绝续玩和创建分支。已有旧Provider回执只有请求/响应、路由及参数，无法证明完整卡片/装配器版本；本批没有据此自动追认任何旧会话。需要使用新会话验证新能力。RPG05合同、宿主和半成品状态保持原样。

## B2 持久化与授权时序

普通会话继续保留原路径；新分支写入 `RPG_DATA_DIR/earth/branches/branch-<hash>.json`，无数据迁移。每个原子封套包括：可续玩的核心session、不可变snapshot及hash、插件节点名称元数据、操作幂等记录。核心会话与插件元数据为独立字段，卸载不删除封套。

后台快照保留完整原始 user/assistant 前缀、turns原文及hash、角色/世界/参数、冻结版本。父路线后续写入不能改变快照。原请求记录和Provider回执通过来源会话ID/请求ID引用，祖先来源会沿分支保留；不会进入子路线可执行requests或模型消息。节点名、分支ID和回执不注入提示词。

保存/分支和发送共享本进程的会话互斥边界。空历史、不完整回合、原文hash不符、生成中、未确认请求和过时revision均拒绝。先完整写入临时文件并flush，再在插件宿主串行队列中复核授权、启用epoch、安装实例、资源和会话revision，原子rename发布。停用先完成时旧结果不得发布；发布已取得串行提交位置时停用在其后生效。票据只可提交一次。

故障注入覆盖发布前失败、临时残片、发布后回执丢失及重启重试。残片不出现在对话列表，已提交操作能返回同一分支。没有声称完成实际断电或跨文件系统容灾测试。仅支持单个自有服务实例管理其数据目录，继续保留B1独占插件owner锁。

## B2 验证与范围

仓库根实际执行：

```powershell
node --test experiments/ai-rpg-engine/studio/branches*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/studio.test.mjs
node --test experiments/ai-rpg-engine/card-replica/tests/*.test.mjs
git diff --check
```

通过：B2核心9 + B2 HTTP5 + B1插件18 + Studio4 = 36；地球卡原有26；合计62，全部离线/mock。包括最小无地球字段合成会话、撤权期间发布、父子互不污染、分支的分支、卸载后读取/续玩、共用额度耗尽及取消迟到结果。mock派发仅在测试专用账本，不是用户真实额度。

独立模块目录执行 `docker build -f studio/Dockerfile -t modelmirror-rpg:plugin-b2 .` 通过，两卡TypeScript/Vite生产构建通过。一次性候选镜像用 `--network none --read-only --tmpfs /data:rw,uid=1000,gid=1000,noexec,nosuid` 验证真实HTTP安装/启用/离线首轮/分支/幂等、卸载/重启读取/离线续玩、两卡页面200和真实Provider used=0，无端口映射或用户数据挂载。镜像运行源码与交付源码一致；最后一个测试及归档文档生成于镜像构建之后。

没有修改父仓前端、作者资源、旧卡片代码、旧公共合同、models.json、provider.mjs、依赖版本或共享配置。未运行正式UI和真实模型验收，未Commit/Push/PR/共享部署。B3将按已批准原型接UI、移除逐份人工发送锁、保存路线草稿，补完整的宿主实际消息捕获和浏览器验收；本批不宣告竖切闭环。

## B2 回退

优先停用插件并保留现候选核心读取能力。会话、分支封套、节点、原件和账本全部保留。B1或更旧镜像不会枚举新branches目录，因此不得未经读取兼容验证直接降级共享部署。此次只构建/运行一次性无挂载容器，未变更共享栈或用户会话；无需要恢复的共享状态。当前交付hash见 DELIVERY.json，先前B1清单原字节保存在 B1-DELIVERY.json。

## 以下为 B1 历史交付说明

下列“后续B2/待确认”等表述是当时状态，由上方当前B2记录覆盖，保留历史而不回写成提前完成。

### B1 生命周期候选

本批只交付审核目录、安装/授权启停、制品绑定、结果有效性和 HTTP 接入。UI v2 已由用户批准；正式 UI、分支快照落盘、分支会话创建、跨版本恢复和连续游玩改动均在后续批次。本批不构成插件竖切完成。

## 制品与能力

- `rpg.branch-save@1.0.0` 为随版本交付的受信代码；没有动态导入、URL 安装、安装脚本或第三方代码沙箱。
- manifest 与 artifact 字节 hash 分开绑定，启动绑定及安装/启用时重新校验；不静默升级。权限为当前会话已完成回合读取、分支请求提案、界面贡献。不开放模型、网络、文件写入或提示词修改权限。
- 适配器目前可生成正文“分支”动作描述及有界的分支请求提案。提案不写会话；B2 必须在核心的会话锁内再次复核授权、版本及 revision 后执行，不能把 `invoke` 成功当作写入权限。
- B1 资源绑定是角色文字、世界、参数、模式的 hash。完整卡片/装配器/模型冻结版本及旧会话资格判断留给 B2，B1 不声称已实现跨版本恢复。

## HTTP 合同

所有写入要求既有同源检查和 JSON 类型；以下路径均从 `/rpg-app` 开始。

| 方法与路径 | 行为 |
|---|---|
| GET `/api/plugins` | 审核目录、权限、兼容范围、制品 hash、当前安装状态和 registry revision |
| POST `/api/plugins/rpg.branch-save/install` | 登记精确制品版本，不启用任何会话 |
| POST `/api/plugins/rpg.branch-save/uninstall` | 撤销所有会话启用，保留数据和操作记录 |
| GET `/earth/api/sessions/:id/plugins/rpg.branch-save` | 当前会话授权状态、两个 revision、最近插件错误代码 |
| POST 上述会话路径 + `/enable` 或 `/disable` | 显式授权启用或撤销；不改变核心会话 revision |

写入共同字段：`operationId`、`pluginId`、`version`、`artifactSha256`、`manifestSha256`、`expectedRegistryRevision`。会话操作另需 `sessionId`、`expectedSessionRevision`；enable 另需与目录完全一致的 `permissions`。未知字段和权限集合拒绝，不接受调用方 URL、system 或服务配置。

重复 operationId/相同内容返回原回执且不重复执行；同 ID 不同内容冲突。历史 enable 在撤销后重放只返回历史回执，不重新启用，消费者随后读取当前状态。安装、卸载和授权共享串行持久化边界；写失败不更新内存授权。操作日志最多 10000 项，达到上限拒绝新增，不静默删除幂等证据。

## 持久化与执行边界

- 当前服务独占 `RPG_DATA_DIR/plugins/`；`registry.json` 保存安装、每会话授权和操作记录。安装范围是这个服务数据目录，不是用户全局系统安装。
- 单文件临时写入、flush 后原子 rename。`owner.lock` 阻止同目录第二实例；正常关闭释放锁。异常退出后残留锁会使插件不可用，核心聊天仍可用。必须先核对进程归属且确认原宿主已停止，才可处理其遗留 owner.lock；不要删除 registry 或会话数据。
- 重启恢复仍有效的明确授权。新会话无授权；卸载后重装不恢复授权。B2 新分支亦不得复制授权。
- 每会话最多一个插件调用，默认 1000ms 超时。插件异常、非法输出和超时只返回固定代码，不传出异常详情；同步阻塞和受信代码已发生的外部副作用不在这个宿主的隔离承诺内。
- 结果绑定会话 revision、资源、安装实例和启用 epoch，并持有宿主短期票据。结果被改写、撤权、重启、资源变化、会话推进后不可使用；最多保留64张票据，较早结果失效。取消信号是协作通知，迟到结果不能恢复有效性。
- 安装/启用状态来自服务端，不由前端布尔开关决定。数据损坏或独占锁冲突时插件 API 返回503，不阻止原卡片创建、读取和聊天。

## 验证（2026-09-19）

从仓库根运行：

```powershell
node --test experiments/ai-rpg-engine/plugins/*.test.mjs experiments/ai-rpg-engine/studio/studio.test.mjs
node --test experiments/ai-rpg-engine/card-replica/tests/*.test.mjs
```

实际通过：插件18项 + Studio4项 = 22项；地球卡原有26项。均为离线/mock，Provider真实调用0。Studio用假fetcher捕获宿主首轮/续轮消息，不将通过结果外推为新插件的真实分支质量。

独立构建 `docker build -f studio/Dockerfile -t modelmirror-rpg:plugin-b1 .` 通过（工作目录为独立 RPG 模块），包含两卡 TypeScript/Vite 构建。候选镜像在 `--network none --read-only`、仅临时 /data 的一次性容器中通过目录、安装、会话启用、两卡页面200及Provider used=0检查，无宿主端口映射和共享数据挂载。镜像ID见 STATUS.json。

父仓前端没有修改，不运行其构建；没有 Compose 配置变化或共享栈部署。镜像构建使用现有锁文件，没有依赖版本变更。Docker 输出中的 npm 升级及 allow-scripts 提示保留为构建提示，未为消除提示更改依赖或关闭检查。

## 回退与下一批

本批核心接线仅 studio/server.mjs 及 Dockerfile 的 plugins 复制行。回退可撤回这两处接线并使用此前镜像；保留 plugins 数据目录、原会话和调用账本。不要混用正在运行的两个宿主访问同一数据目录。

第1批等待用户确认；确认后才进入B2的原子分支存储与运行版本快照。当前无新的 Provider、共享部署、Commit、Push 或 PR 授权，均未执行。正式 UI 尚未接入，原型继续用于已批准的交互基准。
