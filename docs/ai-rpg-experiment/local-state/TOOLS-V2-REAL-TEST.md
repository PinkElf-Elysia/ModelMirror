# 第二轮 Gemini 授权实测记录

本轮离线原型已获用户验收。用户随后授权最多2次 Gemini 调用、每次不超过 USD0.60、总计不超过 USD1.20，不自动重试，并授权临时加密保存选定连接凭据、结束后删除。未部署、未修改共享配置、未提交。

## 结果

- 模型：google/gemini-3.8-flash；Google AI Studio 标准端点；temperature=0.7、top_p=0.8、max_tokens=32768。
- 一个本地首次生成尝试；原桥接回执 dispatched=false。实际供应商调用0次、工具调用0次、续写0次、实际费用USD0。
- 回执 rpg_route_unavailable；底层路由尝试原因为 provider_address_blocked。
- Windows 独立进程解析 openrouter.ai 得到198.18.0.168；现有容器解析得到104.18.3.115。前者属于被出站规则拒绝的保留网段，符合VPN Fake-IP特征；未修改DNS、白名单或出站校验来绕过。
- 没有生成正文或工具参数；原始回复文件为空。未提交增量，丹药保持5份，其他状态不变。
- 本地失败完成约0.772秒。没有正文首字、输入输出token或供应商计费回执；不将其标成真实模型延迟或质量验证。
- 调用器原始 timing.firstContentMs 在空回复失败时误填完成时间。本报告标为未发生，保留原始证据不改写。后续应修正空内容计量，未在本次追加补丁或调用。

## 隔离与清理

共享目录的旧能力记录未声明tools/tool_choice；只在隔离副本中依据当前供应商公开端点文档补齐，未修改共享配置。原认证及关联目录、有效期证据保留，不重新认证。
自动审批拒绝凭据持久化后，用户单独授权临时加密保存；后续自动审批拒绝明文解密主密钥落盘，改用仅驻留进程内存的方式。实测进程已经停止，临时加密凭据目录及本地认证文件已删除。原始材料、请求、回执和统计保留在不跟踪目录。

## 验收入口与证据

- 只读页面：http://127.0.0.1:18825/ （失败记录页，不是成功生成页）
- 运行目录：experiments/ai-rpg-engine/.rpg03-work/tools-real/
- plan.json / first-request.json / initial.json：冻结条件。
- execution-claim.json / isolated-capability-refresh.json：实际选择、公开报价及隔离能力证据。
- transport-evidence.json / stage-*.json / reply-*.txt：原始请求与回执、空回复。
- session.json / result.json / diagnosis.json：状态前后、诊断和清理记录。
- 无自动重试；本次授权未被用来重置或继续派发。

## 下一步

先解决独立运行环境DNS，或使用解析正常且独立数据隔离的测试环境；不降低出站地址校验。用户确认重新实测后再执行，保留本次失败记录及预算边界。原生工具选择、签名续传、正文与增量一致性仍待真实供应商验证。
