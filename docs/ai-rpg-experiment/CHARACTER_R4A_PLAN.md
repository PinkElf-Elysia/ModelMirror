# R4a 开工合同

> 历史阶段记录：下文的零真实调用、待验和基线描述对应当时阶段，原始结果保留。后续已完成并获用户接受的 8 次真实验收见 [真实验收摘要](CHARACTER_R4A_REAL_ACCEPTANCE.md)；最新主线组合、最终范围及剩余门禁以 [收口报告](CHARACTER_R4A_CLOSEOUT.md) 为准。本次收口没有追加模型调用。

基线 `8ed5ed55`，相对已验收 `b84a17d1` 仅增加独立 E6 实验。工作树 `C:/tmp/mm-cr4a`；独立数据 `C:/tmp/mm-cr4a-data`。主线角色、authoring 文件按 SHA-256 冻结于本地 baseline.json。

范围：long-context 新版本合同、参数与任务预算登记、续聊分支、来源/裁剪面板；旧入口只做独立注册与导航。原单聊、群聊、R1/R3b 合同不升级。角色文本、会话、证据不入库。零真实调用，不提交、推送、部署；不开展 R4b/R4c、Agent/世界/Harness/RAG。

批次：纯装配器及容量测试；持久化/配置/预算与恢复；页面与 mock 验收；兼容及帮助文档。每个语义批次最多五文件。风险为持久化与费用归属，必须在调用前验证模型、来源、配置、额度；未知请求保留预留且不重发。

验收：node --test character/long-context/*.test.mjs；相关 character/authoring 测试；父 client 与 card-replica 构建；Windows 短路径与 network-none Linux；中性夹具桌面/窄屏。完整执行结果另记交付报告。

回退：停止本轮预览、撤销独立入口，保留新数据；原目录只读，无数据迁移。旧世界、帮助、截图、CRLF、首次失败/中断及设备延期分别保留，不削弱断言。
