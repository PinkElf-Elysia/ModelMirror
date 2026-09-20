# M2 B5 首份真实输出

B4 已获用户批准；本轮新增独立总额度 4 次。用户明确选择剧情及摘要均使用 Gemini。现已派发 1 次剧情，剩余 3 次；认证 0 次，摘要 0 次，无重试。内容质量待人工审阅。

- 入口：http://127.0.0.1:18473/rpg/earth；虚构角色林遥。会话 `13b3c3ee-76ec-44c3-8480-fa7abae4fd64`。
- 模型：`google/gemini-3.8-flash`；控制面当前可用资格复用，未写共享配置、未认证。
- 实际参数：temperature=0.7、top_p=0.8、max_tokens=16384。top_k、presence_penalty、frequency_penalty、thinking_budget 未发送；context=8192 为卡片设置，不是本轮请求截断策略。
- runtime：`aaca2ad2796eaec07235d85b989358cc3fd64fdf3745a57e4e512587b67d14b9`。B4 原交付118项hash通过；批准前快照保存在本轮 b4-approved 目录。
- 首轮实际只有 system + user，消息与冻结装配逐字一致，作者 system 未附加摘要；首轮角色资料和玩家输入保持现有装配。
- 已保存 1 个完整回合。自动总结已启用，独立摘要模型 Gemini，发送前更新；当前摘要版本 0，符合 T≤1 不总结规则。后续窗口和摘要承接尚未被此次首轮验证。
- 路由 HTTP 200 / complete / retries=0；实际模型与所选一致。回执 token 数为输入18854、输出7243、合计26097，仅为Provider用量回执，未审计账单。
- 原始输出 14835 个 Unicode 字符（包含模型输出的展示标记），逐字保留；hash `6b66607e1d57f1af2384e5caf15618e9029e9ec3aa9dab0faf10768a2a22b7b0`。
- 消息hash `073c7f1f81315b77c56944bd190bc4d3cbf0979705d13d3c5e82aa25dc515e9f`；传输原文hash `1de816a4d1643f14ffb495939c195bbeb86ef9fcb5347a4817d24631f7203022`；控制面规范化hash `935107bdbdfb54381961053d36eb2b5c17be67b43029230af612bfa39abee125`。三者定义不同，不互相替代。

## 本地证据

位于 `experiments/ai-rpg-engine/.rpg04-work/rolling-summary-b5/`：

- AUTHORIZATION.json：本轮授权，限额4，不继承旧账本。
- first-input-freeze.json：虚构角色、模型、参数、摘要指令hash、完整首轮消息与有效策略。
- first-generation-invocation.json：一次发送记录。
- first-verification.json：实际请求、原文一致性和回执核验。
- data/dispatches/earth/slot-1/：派发前占位、request.json、原始response.txt、response.sse及result.json。

浏览器已查看真实首轮、剩余额度、插件和摘要设置；内联截图已观察，本轮尚未独立归档新截图文件（B4真实截图保留）。进入会话时生成仍在进行，界面显示未确认操作；完成后用“恢复记录”只读恢复已保存结果，没有重新发送。

## 执行与边界

新增3个B5工具脚本均通过 node --check；真实首轮 prepare/send 成功。git diff --check 通过。产品源码没有变化，本次没有重复运行B4已通过的全套测试或构建。未 Commit、Push、PR 或更新共享栈。旧M1、B4预览及账本保持原样。

剩余计划为第2剧情、首次摘要、第3剧情，每份结果审阅后再继续。6次建议验证中的第二次滚动摘要和第4剧情未获本轮额度；不能称B5完整通过。回退停用M2，保留历史、摘要、回执及账本；本轮实例PID只作定位，停止前必须重新核对归属。

## 第二份剧情追加记录（2026-09-20T10:10:37.482220+00:00）

首份剧情获用户“通过”，原始记录及当时交付快照保留在 first-approved/。本次只派发1次Gemini剧情，累计2/4、剩余2；无认证、无摘要、无重试。第二份内容质量待人工审阅。

续轮真实请求为 system → 首轮原始 user/assistant → 本轮前置词/输入/后置词，共4条。实际消息与 second-input-freeze.json 逐字一致，旧历史逐字未改，新助手原文与Provider response.txt相同。模型和参数保持已批准值；finish_reason=stop；HTTP200。原文Unicode字符数15676，SHA-256 `e0e7cba35141e8a2c4b6b7878cf6501b37ea935f73d6ec40271d0513513c68a0`。回执用量：{"prompt_tokens": 26330, "completion_tokens": 7609, "total_tokens": 33939}（未审计账单）。

现有2个完整回合，摘要版本仍为0；发送前更新模式下，这是正常状态。接口明确返回 needsUpdate=true / SUMMARY_UPDATE_REQUIRED；下一次摘要应覆盖第1回合，保留第2回合原文。此次没有提前派发摘要，待用户审阅第二份剧情后单独生成并审阅摘要，再继续第3剧情。

证据：second-input-freeze.json、second-generation-invocation.json、second-host-result.json、second-verification.json、data/dispatches/earth/slot-2/。实际浏览器已只读恢复并显示第二回合及剩余2次；本次没有新增截图文件。新增第二次调用工具通过 node --check，实际 prepare/send 成功；产品源码无修改，不重复B4全套测试或构建。未提交、推送、创建PR或部署共享栈。

## 首次摘要追加记录（2026-09-20T10:20:46.160420+00:00）

第二份剧情获用户“通过”，其当时证据与交付快照保留在 second-approved/。本次调用会话 rolling-summary/update，仅派发1次摘要，无剧情调用；累计3/4（2剧情＋1摘要），剩余1次，无自动重试或认证。摘要忠实性待人工审阅，不因完整落盘宣告内容通过。

实际摘要请求与 summary-input-freeze.json 逐字一致：独立冻结摘要指令 + previousSummary=null + 第1回合完整原始user/assistant。未包含第2回合；第1回合的卡内记忆与助手全文一同提供，不单独抽取。Gemini和参数0.7/0.8/16384保持原选择。

有效摘要版本1，覆盖至第1回合；原始摘要与生效文本相同，共1210个Unicode字符，非空、未截断、未超10000上限。约2000字是目标而非自动补齐要求，本轮没有补写、修订或再次压缩。模型finish_reason=stop、HTTP200。SHA-256 `d0cc2d84be45dd6c601f673cebaf7bc21789d18b2ac945197188c53a1a6a0e44`。Provider报告用量{"prompt_tokens": 7543, "completion_tokens": 952, "total_tokens": 8495}，未审计账单。

2个剧情回合及完整持久历史逐字不变。当前策略 ready=true，needsUpdate=false，coveredThrough=1，selectedTurns=[2]：下一次剧情应使用“第1回合摘要＋第2回合原文＋当前输入”。尚未实际派发该剧情，不能将可用策略视为真实承接通过。

证据：summary-input-freeze.json、summary-generation-invocation.json、summary-host-result.json、summary-verification.json、data/dispatches/earth/slot-3/。浏览器设置浮层已显示Gemini、发送前更新、覆盖至第1回合及已保存全文，留供用户审阅，未编辑或保存设置。新增工具脚本node --check通过，prepare/update实际成功；产品源码无修改，全套B4测试与构建未重复。未Commit/Push/PR或共享部署。

## 第三份剧情与本轮额度收束（2026-09-20T10:25:43.837830+00:00）

首次摘要获用户“通过”，当时状态及交付快照保留在 summary-approved/。最后一次剧情完成，累计4/4（3剧情＋1首次摘要），剩余0；所有调用无自动重试、无认证。已停止派发，第三份剧情承接质量待人工审阅。

真实请求与 third-input-freeze.json 逐字一致，共4条：原批准system正文+一次历史摘要数据段 → 第2回合原始user/assistant → 本轮前置词/输入/后置词。第1回合原文未作为独立历史重复发送；批准摘要hash不变，摘要版本仍1、覆盖至第1回合。两份旧剧情、完整原历史均逐字保留，第三份模型原文直接入历史。

Gemini参数保持0.7/0.8/16384；HTTP200、finish_reason=stop；原文16207个Unicode字符，SHA-256 `c91838ff381119259ca700a633554f348565fb2362fe0ab66aa0927ccc51e151`。Provider用量{"prompt_tokens": 28345, "completion_tokens": 8067, "total_tokens": 36412}，未审计账单。证据：third-input-freeze.json、third-generation-invocation.json、third-host-result.json、third-verification.json、data/dispatches/earth/slot-4/。

生成后已有3回合，故下次发送需要将摘要更新至第2回合、保留第3回合；当前needsUpdate=true是正常状态。额度为0，没有执行该更新。浏览器已显示第三回合保存成功、剩余0次、发送禁用。未新增独立截图文件，原B4截图保留。

### 本次发现的界面文案问题

摘要浮层在剧情生成中误显示“正在更新”，聊天也将此描述成未确认的总结操作。`card-replica/src/rolling-summary.tsx:38` 将共享会话busy直接映射为摘要更新，影响用户对额外调用的理解。实际slot-4用途是story，摘要版本及hash未变。记录为非阻断显示问题，收尾修正文案并回归；本次冻结真实运行源码没有改动。证据 ui-status-finding.json。

新增第三轮脚本 node --check 通过，prepare/send 成功；git diff --check通过。产品源码未改，未重复B4全量测试/构建。第二次及后续滚动摘要、回复后后台时机的真实效果未运行；保留离线测试证据，不能用本轮4次结果宣告完整B5计划闭环。没有Commit/Push/PR或共享部署。

## 新增1次授权：第二次摘要（2026-09-20T10:35:41.422278+00:00）

用户明确要求实测第二次摘要，并确认“授权新增1次摘要调用”；本轮累计上限由4增至5，原AUTHORIZATION.json和slot-1…4不改。新增AUTHORIZATION-EXTENSION-1.json，只授权摘要、失败/取消/未知计数、无重试、无剧情。当前5/5用完（3剧情＋2摘要），认证0，剩余0。该授权不代表第三份剧情内容已获通过。

真实增量输入与summary2-input-freeze.json逐字一致：独立冻结摘要指令＋上一已批准摘要＋第2回合完整user/assistant。未重送第1回合原始历史，也未提供第3回合。结果新增摘要版本2，覆盖由1推进至2，保留旧版本1；三个剧情回合及完整原始历史逐字不变。原始摘要与有效文本一致，1768个Unicode字符，SHA-256 `270524e9288b0910ebb78d79627b484d9d90131812757deaaa9ccbf3be04e60b`，finish_reason=stop，HTTP200。参数仍0.7/0.8/16384，Gemini未切换。用量回执{"prompt_tokens": 9522, "completion_tokens": 1436, "total_tokens": 10958}，未审计账单。

接口当前ready=true/needsUpdate=false，覆盖至第2回合，最近原文为第3回合。本次未派发第4剧情。第二次摘要忠实性待人工审阅；这证明首次摘要后的1次真实增量更新，不证明任意长局、进一步滚动或后台更新时间的内容效果。

### 本地额度生效与恢复记录

原服务额度固定在启动参数，需要重启本轮自有入口18471/18473。重新验证PID37068完整命令、空闲状态与已用4次，归档此前状态/源码/会话，再停止自有实例。首次新进程61904因旧owner.lock仍指向已退出的37068，插件初始化拒绝；准备脚本HTTP503 PLUGIN_HOST_UNAVAILABLE，无模型派发；错误退出另出现Node Windows UV_HANDLE_CLOSING。确认原PID已退出后归档遗留锁，仅重启本轮实例，当前PID63440；完全相同prepare成功，随后仅1次摘要update成功。未改注册表、模型资格或共享容器，服务凭据只在内部内存传递。

重启期间前端“状态暂不可用”提示在接口恢复后仍残留，重新加载后可清除；与此前摘要busy文案一并记录为收尾显示修正项。本次不改变已冻结产品源码或自动迁移旧会话。

新增摘要脚本及两个调整后的启动脚本node --check通过，实际prepare/update及输入输出一致性通过；未重复B4产品全量测试/构建。证据summary2-verification.json及slot-5/。此前失败、锁和首次重启记录保留于before-extension/。未Commit/Push/PR或共享部署。

## 本地收尾与最终人工确认（2026-09-20T10:56:26.155020+00:00）

用户“确认，可以开始收尾”已记录为第三份剧情及第二版摘要审阅通过；本轮五份实际输出人工门禁完成。累计仍为 5/5，无新增 Provider 派发。此前“待审阅”段落是当时状态，保留不改写。

两项非阻断显示问题已修正：忙碌文案区分剧情与摘要；接口恢复后清除暂时状态告警，但未知写入告警保留。另保护迟到旧轮询结果不覆盖最新状态。三份前端文件变更，运行 hash `aaca2ad2796eaec07235d85b989358cc3fd64fdf3745a57e4e512587b67d14b9` 不变，作者文本、装配、参数和持久数据不变。

新增回归后宿主/插件/卡片 183 通过、1 项既有跳过；前端 53 通过；两处构建通过。实际浏览器刷新后可读三回合、摘要覆盖至第 2 回合和已耗尽额度；桌面与 390px 手机真实 JPEG 归档在 rolling-summary-closeout。未再派发忙碌任务，相关修正文案以组件回归验证，不能称新的真实生成复测。

第二次摘要证明首次后的 1 次真实增量，不等于至少两次增量、后台时机或更长局通过。未运行的边界见 CLOSEOUT.md。收尾前登记、原文、失败及恢复记录保留；未 Commit/Push/PR 或共享部署。
