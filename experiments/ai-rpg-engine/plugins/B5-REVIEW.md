# B5 收尾：本轮分支存档竖切人工通过

用户对首轮、原路线续轮、分支续轮依次回复“通过”，三份人工意见均已登记。当前已用3/4，剩余1次保留，不再自动派发。原Provider结果保留生成时的manual=pending，后续人工决定存于B5-AUTHORIZATION，未改写原回执。

收尾复核：158项既有交付hash通过；三份完整输出与回执hash一致、重试0；原路线和分支各2回合，共享首轮而各自续玩；分支请求排除原路线第2回合。原路线整个会话文件及分支点快照均未被分支续玩改变；服务端独立账本3/4与三份占位一致。有效运行版本hash保持05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca。原当前登记已存B5-THIRD-DELIVERY.json，后续DELIVERY更新仅反映本次收尾文档。

| 证据范围 | 结果 | 边界 |
|---|---|---|
| B0–B4原型/实现/UI/封装 | 通过，用户已分批确认 | 126项离线/mock、类型检查/生产构建及隔离容器验证为已保存的历史运行结果，本次无代码变化未重跑 |
| B5实际Provider | 通过 | Gemini 3.8 Flash / Google AI Studio，首轮与两路线各一次续玩，共3次，非长期质量保证 |
| B5人工意见 | 通过 | 3份分别获得用户“通过” |
| 本次收尾数据与hash复核 | 通过 | 无新增Provider调用，无修改作者文本/模型参数/UI |
| 全站帮助图片检查 | 失败（历史基线） | 3项其他模块未引用资产仍存在，本次RPG图已通过 |
| 最新主线整合、CI、共享部署和发布 | 未运行 | 发布前仍须刷新主线、处理已知App.tsx交集并复验，不能用本次人工验收替代 |

本轮“可选插件市场原型＋分支存档”候选完成计划中的本地实现、离线/UI验证、真实分支续玩和人工审阅闭环。旧RPG05不补全、不批处理、派发0。此结论仅覆盖本轮竖切，不代表全部项目资源缺口或发布门禁已解决。

预览18433/18435/18437与全部会话、节点、原文、来源回执、授权账本保留。回退优先停用插件并保留当前核心读取能力；不得直接降级到不能枚举branches的旧宿主，也不得重置预算。没有Commit/Push/PR/共享部署；本轮现有代码仍为未提交候选。

以下按原字保留各阶段的待审阅记录，以此处最新人工结果为准。

# B5 第一份真实输出：等待用户审阅

用户已确认B4并新增4次真实竖切额度，本授权只属于本卡本轮，不继承旧卡或上一轮额度。当前已用1，剩余3。失败、取消、未知结果和必要认证计数；不自动重试，不自动花完额度。旧RPG05派发0。

## 派发前冻结

- 当前源码仍为已确认B4候选，开工前113份hash一致；B4-DELIVERY.json原字节归档。
- runtimeHash：05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca
- 模型：google/gemini-3.8-flash；OpenRouter受控适配器指定google-ai-studio，禁止fallback；有效temperature=0.7、top_p=0.8、max_tokens=16384。
- OpenRouter公开无凭据元数据GET返回200，精确Google AI Studio端点status=0，支持上述三个参数；context_length=1048576、max_completion_tokens=65536。元数据检查不是模型派发，没有耗费额度，也没有额外认证调用。
- top_k、presence_penalty、frequency_penalty、thinking_budget仍不发送；context仅原界面设置，不静默截断历史。世界书默认关闭。
- 沿用用户此前提供、去除私密部分的虚构角色龙婉翩，将字段分行供阅读；出生年代/背景矛盾按已批准内容保留。首轮输入“开始我的人生。”没有新添NPC、地点或固定事件。
- 首轮实际wire仅system+user两条；system为用户已批准单句删除版本，user=角色资料+首轮输入，无前后置词；无节点名、工程回执、JSON输出格式附加。
- 角色hash：002e7f81cfc14b4ab38c8d51a1f649e3a38772748461b06a85b1acd6e3fb8ccd；输入hash：47f206e5a2808e86d9e7f9eab220ed551b88d363ad26a11042b7cdeb1db60bc5；装配消息hash：7babb1278bb2b054a623f9e5f23c0121650e356a4d6803e59642d24dca14e140。
- 实际Provider requestHash：24c595583e56311fadaa98091c45a47a03a2085a83301114d47df67ef11d4c14，已对派发前冻结payload逐字段且逐字节核验。

## 调用与保存结果

- 会话：e32c0782-d6ed-4133-a443-0f2ae7e0c16a；请求：b5-earth-open-20260919。
- HTTP 200，实际模型google/gemini-3.8-flash，供应商Google AI Studio，finish=stop，done=true，重试0。
- prompt_tokens=19151，completion_tokens=8297（含reasoning_tokens=831）；上游报告cost=$0.045477，未做独立账单审计。
- 原文24673 bytes、15436字符，完整原文/SSE/请求/派发前reservation与result均保留在`.rpg04-work/plugin-b5/data/dispatches/earth/slot-1/`。
- rawHash：c944f9c87d11240c9b5ff3872dcf96877c83d8ebf534c9b380c91c72be45fb29；sseHash：d1810d9a72afe1ce1070672323a275ba3d350975110f354bcc06a21cf4106347。
- 服务历史中的原文与Provider文件逐字节一致；浏览器“查看原文”文本SHA256也一致。正文安全展示与原文分离，未补写或修复。
- 原文中的标签、校验声明、建议等均为模型输出，不作为Agent指令或验收通过证据。

## 本轮审阅入口与停止点

http://127.0.0.1:18437/rpg/earth → 历史 → 龙婉翩（1回合）。已由正式浏览器打开。此入口是本轮只读审阅代理，禁止额外POST；正式产品连续游玩实现未改。旧18433/18435及共享服务均保留。

本轮独立后台18436，凭据仅由服务内部从既存主工作区server/.env载入内存，没有输出或写入新文件。服务通过一次性许可hash验证实际请求，在fetch前以独占文件占位；原Provider亦在派发前写入原卡账本。相同许可不能再次调用，后续许可仅在用户审阅后准备。

独立启动器首次语法检查因Windows分隔符转义失败，未启动、未读凭据、未派发；使用path.sep修正后通过，再启动。没有把该失败计作模型调用。

这份仅完成真实首轮/零插件路径；尚未安装或在真实会话启用插件，尚未创建真实分支点、原路线续玩或分支续玩。用户对叙事、表/里世界与显示的人工意见待收集，不自动判定质量通过。下一步在用户审阅后保存分支点，分别生成原路线与分支续轮；每份输出仍须审阅。第4次为上限内保留，不预定消耗。

本轮无提交、Push、PR、共享部署或作者文本调优。公开交付状态见STATUS，原始模型输出与试运行数据保持忽略归档。

## 首轮已通过；第2次原路线续玩待审阅

用户随后明确回复“通过”，只登记为slot-1人工通过；上述首轮待审阅文案是当时记录，原Provider result不改写。开工前131份交付hash一致，原登记另存B5-FIRST-DELIVERY.json。字符计数说明：首轮15436 Unicode码点/15447 UTF-16代码单元，原文hash始终一致。

通过正式18436 HTTP接口安装审核插件、对原会话明确启用，并从第1回合创建“龙婉翩 · 另一条路线”。分支ID：branch-83354c14853873f6893ee3448a3175fa4121071fc70e5a62a3275dcc04d63836；snapshotHash：5bf7fc423887cacb9a6ed9385cbabdd29f8b9fc36e831f2eaf9fd14be3a737e0。创建消耗0次；新路线requests为空、插件未启用，冻结配置与首轮完整原始历史逐字一致。

第2次仅原路线输入：“我先去洗漱、更衣，再到二层餐厅吃早餐，边吃边听苏曼把今天的安排说清楚。”。派发前冻结完整payload；实际请求是system → 首轮user/assistant原文 → 本轮user，当前user严格等于官方前置词+输入+官方后置词，无重复角色卡或分支名。模型、参数、世界书与源码保持原冻结版本。

- HTTP200，google/gemini-3.8-flash／Google AI Studio，finish=stop，done=true，retries=0。
- requestHash：53d77e8178e8e07572669108097239da8c51d23450ea4477fcaf2db496a7ae85；rawHash：6145fb25d100a974454971e7769f81706498921723d01d8dd4a9cb8eca591c5d；sseHash：dfdb853c4aafb265af1ab7b208823a3a5d89d8c51e53620b4aa2177c7de92650。
- 原文27668 bytes，16890 Unicode码点；prompt_tokens=27105，completion_tokens=8418，上游报告费用$0.05189625，非独立账单审计。
- Provider原文、会话历史与浏览器“查看原文”hash一致；没有修复或补写模型内容。
- 续玩后父路线2回合、分支1回合，整个分支文件hash未变化。浏览器普通对话列表可见两路线，切换分支再返回父路线通过，无新增派发。

完整输出：.rpg04-work/plugin-b5/data/dispatches/earth/slot-2/response.txt。当前网页已返回父路线第2回合供审阅。已用2/剩余2，停止继续派发；分支续玩尚未运行，第2份质量待用户人工意见。本步未改核心源码，执行的是实际HTTP/保存/浏览器核验，B4的126项测试为历史证据。无Commit/Push/PR/共享部署。

## 原路线续玩已通过；第3次分支续玩待审阅

用户随后回复“通过”，登记slot-2人工通过；原Provider回执保留生成时的pending。开工前145项当前交付hash一致，旧登记保存为B5-SECOND-DELIVERY.json。

分支本轮输入：“我暂时不去吃早餐，先坐到彭博终端前，调出赵律师下午要讨论的信托架构底稿，仔细查看其中的条款。”。使用新请求ID b5-earth-branch-20260919，原路线请求只保留来源证据，不复制执行。分支插件保持未启用，普通聊天仍可继续。

实际wire为system → 首轮user/assistant原文 → 本轮user，共4条；排除原路线第2回合，当前user严格为前置词+输入+后置词，插件名称/路线名/工程回执未进入模型消息。角色、模型、有效参数、作者文本及世界书策略均未改。

- HTTP200，google/gemini-3.8-flash／Google AI Studio，finish=stop、done=true、retries=0。
- requestHash：9824b1c5a32473807ebdb0c9316adb3619b918e6c24af0fe280d15ec379d8592；rawHash：63cc787ed1a3bcbdf3b52662d692de42bfff2e1675f08efa2ddfa7f83435454f；sseHash：08ed9e16202b036713e929a9a7d07dca9fa631c8ccd59691dfb6abdb3aa171dc。
- 原文29133 bytes、17466 Unicode码点；prompt_tokens=27109、completion_tokens=8732。上游报告费用$0.05307675，未独立审计账单。
- Provider原文、分支历史、浏览器“查看原文”hash一致。原路线整个会话文件hash未变；分支不可变snapshotHash未变，双方各2回合、共享首轮，新增各自独立续轮。
- 普通对话列表显示父路线和分支；浏览器已切换“龙婉翩 · 另一条路线”供审阅，页面剩余1次。

完整输出：.rpg04-work/plugin-b5/data/dispatches/earth/slot-3/response.txt。已用3/剩余1；第4次不预定消耗，无重试或进一步生成。第1/2份已人工通过，第3份人工意见待收集，不能提前宣告整个竖切或初版验收完成。

本步只更新验收数据与文档，未重跑无变化的B4核心测试；实际请求、持久化隔离和浏览器核验通过。历史126项离线/mock、3项无关帮助图基线失败、上游整合缺口均保留。无Commit/Push/PR/共享部署。
