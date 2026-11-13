# C2-B 后续剧情配置、高级参数与六卡锁恢复

状态：本批实现和离线验证完成，待用户验收；不代表真实供应商与整个最终验收通过。

## 范围、基线与保留

- 工作树：C:/tmp/modelmirror-rpg-comparison-c2b；分支：codex/rpg-comparison-c2b。
- 继承已验收 C2-A，HEAD 为 d9f20fe691e8ddef8623d779a25a9359717f2e8d。实施前复制并逐份校验 111 个已接受修改文件，没有迁移原会话。
- C2-B 共 24 个增量文件，含本文。两类宿主的原子配置、统一进程锁及验证需要跨模块接线，超过约五文件的建议；没有夹带新插件、新依赖或控制面业务策略变更。
- 原工作树的 111 个文件最终 SHA-256 均与实施前一致；18662、18664、18666、18668、18676、18677、18678 的有效入口最终 GET 均为 200。
- 未新增真实调用、认证、共享部署、Commit、Push、PR 或数据库迁移。全部供应商出口为本机 httpx.MockTransport；样例额度不是用户真实额度。

## 原站观察与本地约定

在原站测试作品 3fa3aa48-8e15-475f-9f72-27d391aa6bcf 的“添加模型”弹窗实际操作：

- 核对温度、Top P、Top K、频率/存在惩罚、输出长度、上下文长度、思考预算。
- Top K 从 40 键盘增加至 41，再还原 40；思考预算从 16000 减至 15999，再还原。
- 模型列表为二级搜索窗口；Esc 先关闭列表，再退出参数弹窗。没有保存作品或派发。
- 当时可见范围：温度 0–2、Top P 0–1、Top K 0–100、惩罚 -2–2、输出 1–32000、思考预算 0–100000。这些只是观察值，不替代本地控制面按模型提供的能力。

本地新增的默认/显式区分、停止词列表、思考类型约束、跨模型预设冲突及“设为后续剧情配置”事务确认，属于已批准的新设计，不宣称原站有相同算法与权限语义。

## 后续剧情配置

完成或放弃本轮，且其他候选及后台任务收束后，已完成候选可“设为后续剧情配置”。确认窗口展示当前配置、将采用的模型与显式参数。

- 创作调试作为原生能力，不要求插件。
- 聊天必须同时具备“模型选择”和“生成参数”的会话授权；仅有多模型对比权限不允许修改会话默认。
- 服务器从已落盘候选读取完整选择，前端只提交候选 ID、操作 ID 及会话、模型、参数、授权 revision。
- 保存前重新核对模型资格/能力与容量。能力变化或不兼容明确拒绝，不转换或丢弃参数。
- 模型选择、剧情参数覆盖和原操作回执一次落盘，正式会话 revision 只推进一次。两项授权在提交前一并校验，撤权或多标签冲突时整体拒绝。
- 摘要和记忆模型/参数不变；正式剧情、变量、CG、M2/M3 不因设置而推进。
- 相同操作回放返回原回执；丢失响应时只 GET 原操作结果，不另建 ID 或自动重写。
- 关闭或取消不应用；刷新后保留已确认配置。普通发送使用新配置，对比记录和已经采用的回复保持原文。
- 未决写入占用浏览器恢复日志；不能以“未查询到”擅自认定失败并重发。

接口位于既有两类 comparison 根路由：

- GET /{session}/{batch}/future：当前实际配置、CAS revision、权限与收束状态。
- POST /{session}/{batch}/future：原子应用候选配置。
- GET /{session}/{batch}/future/{operationId}：只读核对原操作。

新增可选持久字段 futureConfigurations 保存精确意图和回执；没有为旧服务静默迁移冻结记录。

## 高级参数交互

- 数值输入与滑杆联动；整数步长为 1；范围由模型能力提供。
- 每项显示默认/显式状态。模型默认省略值明确显示“不传此参数”，有声明默认值时显示该值。
- 停止词按字面保存，包括前后空格；中文输入、Enter 添加、逐项删除、重复、数量、Unicode 长度及未添加缓冲区均处理。输入法组词中的 Enter 不触发添加。
- 添加/删除停止词后焦点回到输入框；默认开关重绘后保留键盘焦点。
- 切换模型保留原值。不支持的字段及枚举值仍可见，错误明确标记；处理冲突前禁止应用。
- 实际预设数据结构接通；跨模型载入保留值、展开高级区并提示核对。载入预设或取消不会改变会话默认。
- 依赖和互斥在字段与底部提示，使用面向玩家的参数名称。
- 新开一轮清除上一轮预检报价；候选草稿仍保留，必须重新预检。
- 390px 下全屏、正文滚动、底部动作保持可见；停止词添加按钮不换成竖排。参数保存成功后清除过期的忙碌诊断，不清除服务端操作记录。

## 六卡异常退出恢复

插件注册表 owner.lock 补齐与已有会话存储一致的保留式恢复：

1. 只对有效进程号、确定返回 ESRCH 的死亡进程恢复；活进程、无法判断的进程拒绝接管。
2. 校验目录祖先无符号链接，锁为单链接普通小文件；打开后再次核对文件身份与内容。
3. 用独占恢复声明协调竞争者，复核旧锁后改名为 owner-dead-*.json 留证，再独占创建新锁。
4. close 只移除自己持有、内容未变化的锁。
5. 六卡生成宿主默认启用这一恢复；其他插件宿主仍需明确 opt-in。无需删除会话、授权、候选或账本。
6. 若恰在恢复中再次崩溃留下无法确认的 owner-recovery.lock，仍安全阻断。不能声称所有损坏锁都能自动接管。

实际子进程测试：六卡各有两份已派发但响应未知候选，直接终止宿主进程；重开时自动归档旧锁，保留授权、已用额度及 12 个未知请求，没有重新派发。随后按原 ID 核对并逐卡采用，正式剧情只前进一次。

## 验收入口

均为新建离线样例，旧入口维持原样：

- 创作：http://127.0.0.1:18680/?work=work-15c90df72fcd8d43331acf35dc7b4e4f75d78cf99bc179f7a546323f34248064
- 自建作品聊天：http://127.0.0.1:18681/chat?session=session-69e0d205c05443602afe0484baf7b63ec703e0a0230f9e7b8fc6e7f8a4c7fcf9
- Earth：http://127.0.0.1:18682/rpg-app/earth/?session=f51d53af-76e5-44b0-9fbe-cd36398953b2
- 六卡完整链接见证据目录 acceptance-entries.json。

创作、聊天和 Earth 均已有一轮对比记录及后续普通发送。可直接回看“对比 1”，打开候选配置确认，取消不会新增调用。

独立六卡夹具未挂载主站 /rpg/plugins 页面；模型选择已通过行间的本地插件市场安装，Earth 的模型/参数授权通过其会话界面完成。正式共享栈的主站市场不在本批部署范围。

## 验证结果

证据目录：C:/tmp/modelmirror-rpg-c2b-evidence/。样例、完整原文、截图和账本不进入版本控制。

| 检查 | 结果与证据 |
| --- | --- |
| 参数持久化、三用途、授权、插件锁、六卡宿主回归 | 103/103；parameter-regression.txt |
| 对比协调、HTTP、预设、浏览器日志、参数界面回归 | 92/92；comparison-regression.txt |
| 六卡 future 适配器专项 | 1/1；含 18 份模拟请求捕获，两个授权、原子配置及下一次普通发送逐卡验证 |
| 六卡真实进程终止/重启专项 | 1/1；保留 12 次未知请求，恢复新增派发 0 |
| 最后文案/焦点修正后的界面恢复回归 | 14/14（属于上述 92 项的子集）；ui-polish-regression.txt |
| 六卡构建 | TypeScript + Vite 通过；card-build-final.txt，既有动态/静态导入警告保留 |
| 浏览器样例 | 创作/自建聊天/Earth 各 2 候选 + 1 普通发送，共 9 份模拟请求；活动请求 0 |
| 公共消息 | 三批内两份候选 messagesHash 一致；request-summary.json |
| 设置真实生效 | 创作普通发送保留模型 B、温度 0.6、精确停止词和思考预算；聊天 0.3；Earth 0.5 |
| 正式状态 | 三类会话均 2 回合、1 次后续配置回执；future-receipts.json |
| 旧入口/源码 | 七入口 200、111 个源文件未变；protected-entries.json |

主要命令（仓库根目录）：

~~~
node --test --test-concurrency=1 --test-timeout=180000 experiments/ai-rpg-engine/authoring/generation-session.test.mjs experiments/ai-rpg-engine/plugins/host.test.mjs experiments/ai-rpg-engine/plugins/owner-lock.test.mjs experiments/ai-rpg-engine/studio/card-generation.test.mjs
node --test --test-concurrency=1 --test-timeout=180000 experiments/ai-rpg-engine/authoring/comparison-ui.test.mjs experiments/ai-rpg-engine/authoring/comparison-ui-http.test.mjs experiments/ai-rpg-engine/authoring/comparison-host.test.mjs experiments/ai-rpg-engine/authoring/comparison-coordinator.test.mjs experiments/ai-rpg-engine/authoring/parameter-ui.test.mjs
node --test --test-concurrency=1 --test-name-pattern="C2-B" experiments/ai-rpg-engine/studio/card-comparison.test.mjs experiments/ai-rpg-engine/studio/card-restart.test.mjs
cd experiments/ai-rpg-engine/card-replica
npm.cmd run build
~~~

浏览器覆盖：
- 创作：跨模型保值/不兼容枚举、Top K、停止词增删/重复、思考预算依赖/互斥、取消/Esc、预设保存及跨模型载入；候选采用、未来配置确认、普通发送及刷新。
- 聊天：缺少参数授权时确认禁用；安装/启用后应用成功；刷新保留，下一次普通发送参数一致。
- Earth：只启用比较完成候选；补齐模型/参数授权后确认应用、刷新及下一次普通发送。行间本地市场安装不替其他会话自动授权。
- 390px 创作参数面板宽度和文档宽度均为 390；底部可见。缩小至 390×480 后操作区仍位于可视范围。回到 1280×720 无横向溢出，顶部按钮未重叠。
- 不把桌面视口缩小等同于真实手机输入法/软键盘验收；未穷举所有设备。
- 六卡逐卡恢复与参数真实性使用受控 HTTP/进程专项，未把单卡浏览器观察宣称为六卡各自完整视觉验收。

本轮发现并修正：模型选择授权未纳入原授权快照、模型/参数半保存风险、预设字段结构不一致、切换模型隐藏不兼容枚举、高级区未展开、停止词焦点丢失、上轮预检残留、手机按钮换行、成功后残留忙碌诊断。早期一次回归运行期间仍在编辑受 hash 绑定的源码，触发预期的源码变化拒绝；停止相关编辑后完整重跑通过，没有放宽校验。

## 文件与回退

准确 24 文件路径及 SHA-256 见 delta-manifest.json：

- 原子配置/宿主：comparison-future、comparison-host、comparison-client、joint-session/state、chat-parameters、card-generation、card-comparison。
- 生命周期：plugins/owner-lock、plugins/host、studio/generation-cards、studio/server。
- 界面：comparison-ui.mjs/css。
- 夹具与测试：generation-session、comparison-ui、owner-lock、card-comparison、card-restart/fixture、comparison-test-support、comparison-ui-fixture、server/tests/rpg_comparison_fixture.py。
- 文档：本文。

首选回退是关闭新增比较入口或停用相应会话参数权限，保留候选、未来配置回执、正式剧情和账本。已派发/未知请求仍由原操作核对。旧二进制降级只在副本上验证，不回写本批的新状态。

剩余门禁：用户本批验收；最终另行授权的真实模型/高级参数供应商支持、完整插件组合及费用/取消能力验证。本批无上述真实调用授权。
