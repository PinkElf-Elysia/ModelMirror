# R18公网发现审批模板

执行`discover:r18`前必须填写并向用户披露：

- 固定查询清单及其SHA-256；
- 访问主机、每主机请求上限、总请求上限和超时；
- 是否下载公开源码归档或仅读取元数据；
- 仓外输出目录及失败清理方式；
- 明确声明不登录、不携带凭据、不调用商业产品API或供应商接口。

审批只覆盖该次发现，不覆盖候选依赖安装、容器执行、外部模型调用、提交、push或PR。

## 2026-08-26执行记录

- 用户批准的主机与硬上限：`api.github.com=48`、`github.com=40`、`godotengine.org=4`、`docs.inworld.ai=2`、`docs.convai.com=2`、`developer.nvidia.com=2`、`rosebud.ai=2`；总上限100，单响应2 MiB，总响应128 MiB，单请求15秒。
- 全程未登录、未读取或发送凭据，未调用商业产品API、OpenAI、Marble或Meshy，未clone仓库或安装依赖。
- 初始整链在旧Inworld/NVIDIA路径跳转处fail closed，未发布输出；旧查询随后被证伪为七个赛道空召回，保留为`discovery-query-set-approved-docs-v1.json`。
- 官方文档修正为无登录、无跳转路径后8/8通过；仓外证据目录名为`matrix-oasis-r18-discovery-20260826-documents-v3`，query set SHA-256为`e77adaea88068ece7a786c9157315fcd9d3e757bc4b54b5b8379130e273609d2`。
- 宽召回GitHub查询集SHA-256为`0a41b8f7364b2a1ac08d364be25fa89f5d9affeef054a53722ec2912e432f4e6`；搜索证据目录名为`matrix-oasis-r18-discovery-20260826-search-v2`。搜索结果只形成长名单，不自动成为短名单或推荐项。
- GitHub未认证core配额在首次失败链后归零；实现未使用token绕过，而是拆分搜索与身份锁并等待公开配额自然恢复。
- 配额重置后按确定性跨赛道轮询选取23个唯一仓库，23次commit/tree身份查询全部成功、零失败；身份目录为`matrix-oasis-r18-discovery-20260826-identities`。
- 仓外证据SHA-256：搜索报告`c8a2926973c4cbe8658566eefeee06e3425d1060a12a5eba2256658d44cceefc`；身份报告`709584ff3c0500cb87ff3dae8680f23c0414e95653e48f4ca3df70c47b8443de`；身份数据`7c646dbc26e3e3b807183bfda441dbfbe31998701c5409b0cb5668c77a01b3dd`；文档报告`c5dee3f533bf7fc9445539e77579b30f0659249e8e458e1a2d70dfc075082721`；文档数据`cd128972711fa67cf72c1d07c04ea677fd933cdbe664d7b70069da651f9b8bf7`。
- 仓内只保存脱敏来源锁，SHA-256为`0e4e8bc4b2209c4a9a984740f716f3cfcb6212e1aa4c4ce4806df4b1cb9ed797`；其49个唯一候选/82个赛道条目只证明来源覆盖，不代表短名单或正式集成资格。
