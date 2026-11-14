# R5 Gemini 有限实测

2026-11-14，用户通过离线原型验收，授权补齐有限接入及最多三次真实请求。
槽位 1/2/3 上限 $0.15/$0.15/$0.30，总上限 $0.60。第一阶段直接准备有效提案时跳过槽 2；只执行槽 1、3，总上限 $0.45。不自动重试、不切备用、不追加总结或记忆调用。
正确提案由脚本按预先授权确认一次，限定 use_medicine / medicine / quantity=2 / 5→3，其他状态不变。

模型 google/gemini-3.8-flash，Google AI Studio 标准端点；每次独立运行先重新核对端点报价与路由资格。query profile=core-query-r3 最大输出 8192，叙事 profile=god-long-v1 最大输出 32768，temperature=.7，top_p=.8。

## 有限接入

新增 actions/real 的 provider、运行器、独立协议、桥接、启动器及只读页面。ActionHost 增加显式 controlledReal 开关及真实统计识别，原 offline 入口仍不能接收真实 provider。
协议复用本轮 JS 工具 schema／验证／分片解码，通过固定本地 Node 入口处理有界 JSON；不修改父项目工具白名单或公开端点。Python 任务路由复用原认证、资格、SSE、回执与出站校验。
固定桥接仅注册在本任务 FastAPI 实例，没有注册到正式应用。无新依赖、数据库迁移或共享配置修改。

选定连接凭据只读复制为临时加密快照，主密钥只在进程内。结束删除 router 凭据目录与 local-auth；回复、工具调用和统计保留在忽略目录。路径为 .rpg03-work/actions-r5-{mock,real}-*，不复用历史已消耗额度。

## 派发前验证

新增 Python 协议 8/8；Node 预算 2/2。预算测试最初缺少授权 ID，补齐测试夹具后通过，没有放宽运行校验。
完整 mock-query 已通过三阶段，实际路由资格／预算／出站校验均经过，仅上游响应替换为 MockTransport。预留约 $0.478，不是实付费用，临时凭据已清理。
mock-direct 单独验证首个请求直接提出 prepare_action 时跳过查询续写，只需两次生成。该路径已通过；结果见下文。

E2 主会话收到的收尾交接表明其 PR #477 已提交；此处仅记录来信，不视为已合并，也不修改探索树。E2 confirmation 只确认方案，requirements/expected 不是可执行动作；本轮不执行赠送、不导入角色、不使用其历史额度。

## 验收与边界

保存冻结输入、工具原始消息、元数据、查询结果、确认记录、状态、供应商 SSE、token、费用及计时。正文首字没有可靠边界则 null，不用工具文字冒充正文首字。估算和实报分别记录。
独立审阅端口预留 18874；页面只读，不包含发起调用按钮。原离线 18871 保留。
用户真实页面验收未完成。不提交 PR，不部署。

## 本次实测结果（2026-11-14）

完整模拟派发先通过两条路径：mock-query 使用槽 1、2、3，mock-direct 使用槽 1、3；均只确认执行一次，丹药 5→3，其他状态不变，叙事完成。模拟费用为预算预留，不是供应商实付。

真实运行仅派发槽 1。Gemini 原生返回 prepare_action，参数为 actionId=use_medicine、itemId=medicine、quantity=2；该选择符合测试目标，但本次桥接未成功接收为有效方案。供应商结束后还发送了携带 usage、相同 finish_reason、无新增正文的末帧，原解析器以 TRANSPORT_AFTER_FINISH 拒绝，桥接保留 unknown。脚本以 R5_NO_VALID_PROPOSAL 停止，未自动重试、未占用其余槽位、未确认执行、未生成结果叙事。

| 指标 | 本次实际结果 |
|---|---|
| 模型 | google/gemini-3.8-flash，Google AI Studio 标准端点 |
| 请求次数 | 1 / 最多 3 |
| 输入 / 输出 token | 37701 / 223 |
| 输出中推理 token | 194（已包含在输出中，不重复累计） |
| 供应商实报费用 | $0.029112；槽 1 上限 $0.15 |
| 槽 1 派发前预留 | $0.129016（非实际费用） |
| 请求完成时间 | 8.728 秒 |
| 首次可见文本 / 正文首字 | 未测得：本次只有工具调用，无正文 |
| 实际状态 | 丹药仍为 5，其他状态不变，执行事件 0 |

已做有限离线兼容修正：接受相同结束标记、没有新增内容的用量末帧；仍拒绝结束后新增正文或工具；保留原生 reasoning_details 续传信息；空文本不再把完成时间误当首字。原始失败回执、SSE、原代码副本和原计时均保留，审计后的计时单列。离线重放正确提取原生调用并预览 5→3，但没有执行或把原 unknown 记录改成成功。

本批验证：
- Node actions 与 real 测试 26/26，通过。
- Python real/test_protocol.py 8/8，通过。
- 原始 SSE 离线重放通过；供应商元数据续传仅离线验证，未真实续写。
- 独立页面 HTTP 200，浏览器无脚本错误，工具展开、证据读取及刷新通过，没有派发按钮。
- mock-query、mock-direct、real 三个运行目录的临时凭据与本地 token 均删除；没有修改共享配置。
- 先前离线原型的 128 项证据仍属前一批，不冒充本批重新运行。

### 证据与交接

独立只读验收页：http://127.0.0.1:18874/ 。原离线交互页 18871 保留。本页展示的是部分真实验证及失败证据，不是完整真实闭环通过。

非跟踪证据目录：experiments/ai-rpg-engine/.rpg03-work/actions-r5-real-v1。
原始：execution-claim.json、stage-1.json、transport-evidence.json、session.json、dispatch-audit.json、cleanup.json、source-before-fix/。
派生：assessment.json、offline-replay.json、review-browser.json、review.png。派生报告不得替代原始失败结果。
模拟目录：actions-r5-mock-query-v1、actions-r5-mock-direct-v1。

尚未验证：真实确认执行及回执后的正文一致性。后续补测需另行明确决定；本批不继续使用剩余调用槽，不增加其他补丁、模型调用、发布或部署。

原生工具续传元数据参考：[OpenRouter 官方说明](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/reasoning-tokens.mdx)。没有新增 SDK 或复制第三方实现。


## 后续恢复补测

用户另行授权后已完成一次正文补测，费用 $0.03974325。保存提案恢复后确认执行一次，丹药 5→3，其他结构状态不变；正文残余偏差保留。上述首次失败记录不变。见 [R5-RECOVERY-VALIDATION.md](R5-RECOVERY-VALIDATION.md)，新验收页 http://127.0.0.1:18875/ 。
