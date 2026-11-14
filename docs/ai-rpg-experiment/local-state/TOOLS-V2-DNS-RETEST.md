# DNS 修复与重新授权实测

## 已完成的环境修复

只为本任务独立桥接注入 ProviderEgressPolicy 已有 resolver 接口，通过现有容器只读查询 openrouter.ai 的DNS结果。未改Windows DNS、hosts、VPN、容器配置或共享路由。

取得公网地址104.18.3.115；原出站策略继续校验并固定目标地址，保留Host/SNI和TLS验证。空内网白名单。198.18.0.168、127.0.0.1、10.0.0.1、169.254.169.254四项拒绝检查通过；以匿名GET读取公开标准端点报价返回HTTP200。这不是模型调用。

## 新授权执行结果

用户重新授权最多2次、每次USD0.60、总计USD1.20，不自动重试。使用原始开局、用户输入、模型和参数，原冻结请求文件字节不变。新目录独立记录，不覆盖旧失败证据。

一个本地调用尝试返回rpg_receipt_pending（外层unknown）；核对独立数据库原attempt，dispatched=0、status=failed、result_class=preflight_failure，具体原因为provider_chat_policy_or_qualification_changed。由此确认实际供应商调用0次、费用USD0；没有第二阶段，没有模型原生工具调用或正文生成，丹药仍为5份。

诊断发现隔离复制不完整：保留的策略登记3个稳定模型，而副本仅有选定连接的1份模型资格。读目录时单一Gemini资格看似可用，但完整派发前资格一致性检查使请求失败。修复DNS不能替代完整派发前演练。本轮未放宽检查，未伪造其他资格，未额外复制未授权连接凭据。

本地失败完成约2.829秒；正文首字未发生，token无供应商数据。保留原始unknown回执并单独保存账本核对结论；不改写原始记录中的时间字段。

## 凭据与证据

已停止本任务桥接，删除本轮router目录及local-auth.json。解密密钥只在进程内存，不落盘。保留独立请求、原始空回复、回执、阶段统计和DNS检查；未改共享配置、未部署、未提交。

- 页面：http://127.0.0.1:18826/ （只读失败记录及DNS验证）
- 目录：experiments/ai-rpg-engine/.rpg03-work/tools-real-dns-v1/
- task_dns.py / check_dns.py / dns-preflight.json：进程级修复和验证。
- first-request.json / plan.json / renewed-authorization.json：冻结输入及新授权。
- transport-evidence.json / stage-*.json / reply-*.txt：原始记录。
- dispatch-reconciliation.json：dispatched=0原账本证明。
- diagnosis.json / result.json：诊断和未变状态。

## 剩余项

原生工具选择、Gemini签名续传、正文与状态一致性仍未得到真实模型验证。下一次应先解决隔离策略依赖完整性，并用模拟供应商跑完整的begin、再次校验、mark_dispatched及回执链路；单纯catalog.available=true不足以代表可派发。新的真实调用须经确认，不能把本次0费用当作自动重试授权。

## 后续根因更正

本记录中“缺少其他模型资格”的归因是当时的初步判断。后续回归已精确复现：删除前序路由造成运行时 position=0 与数据库 position=1 不一致，才是本次派发失败的直接原因。完整顺序加禁用、无凭据的公开连接引用即可通过原校验，详见 [隔离环境修复与模拟证据](TOOLS-V2-ISOLATION-FIX.md)。原始失败记录保持不变。
