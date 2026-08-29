# R19参考来源二次核查

R19重新核查R18中与权威边界直接相关的固定来源。仓库只保留身份与原创结论，不复制候选源码。

- AI Town固定`7b242334…`：Engine独占状态，Agent通过input进入，适合参考“提案不等于状态”。核查时上游main为`8e05997f…`，固定版本之后的漂移不改变R19来源锁。
- Concordia固定`44904ecb…`：ActionSpec与resolve分离有参考价值；模型Game Master返回的文本不能成为确定性Runtime权威。
- SOTOPIA固定`a0aaafb4…`：环境、行动和评估分层只用于测试设计；随机顺序和模型评估不进入R19。
- CloudEvents固定`fc1f6f31…`：仅借鉴事件元数据；R19不宣称CloudEvents兼容，连续revision、CAS和哈希链由本地闭合合同定义。
- LangGraph固定tag `1.0.8`提交`a7a27dd4…`：checkpoint replay不能替代Runtime逐Action重放；许可证闭包未在R19证明，只作行为警示。
- Kurrent公开文档（Node客户端v1.3追加、Server v25.1 projections）：expected revision、幂等追加和projection可重建是设计模式，不引入Kurrent服务、数据库或SDK。文档是版本化公开资料而非提交锁，因此不把它计为可复用源码证据。

所有项目在R19均为`reference-only`，没有生产依赖、复制源码、容器或运行服务面。`reference.lock.json`自身也由完整字节SHA-256锁定，commit、tree、archive、许可证或用途任一漂移都会使验证失败。
