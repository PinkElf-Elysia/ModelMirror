# Prototype Generator

R8 的私有自然语言原型生成包。公开面固定为供应商中立的 `generatePrototype(request, provider)`、OpenAI兼容Provider工厂和静态operational error。

Provider 固定调用配置 endpoint 的 `/v1/chat/completions`，使用非流式 strict JSON Schema 响应，不使用 tools、函数调用、redirect 或自动重试。包本身不读取环境变量，也不持有父仓网关配置。

普通测试只连接 loopback 假服务。任何真实模型调用必须先完成 `docs/MODEL_CALL_APPROVAL.md` 的当次人工审批。

生成编排最多执行一次初始请求和两次定向修复；有效Proposal继续通过冻结Compiler、Runtime prepare和初始Session检查。成功只返回五个canonical JSON字符串，失败只返回静态、排序稳定的内容diagnostics；依赖故障统一为`PROTOTYPE_GENERATOR_INTERNAL_ERROR`。
