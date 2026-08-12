# R10 Prototype Builder 威胁模型

## 受保护对象

- 模型、Meshy、Marble凭据与费用额度；
- prompt、Blueprint和供应商返回；
- 当前可运行run、规范化GLB与Godot进程；
- 模块、父仓、其他worktree和共享栈。

## 主要威胁与控制

- 外站驱动loopback：仅exact same-origin、JSON content type、HttpOnly/SameSite cookie；无CORS。
- 审批漂移：模型审批绑定prompt SHA；环境/资产审批绑定Blueprint SHA与精确brief、模型、请求和费用上限。
- SSRF与下载炸弹：固定API host、受控官方资产host、无redirect、流式字节上限、PNG/GLB离线复验。
- 半成品发布：同父staging、FileHandle、bigint身份、realpath containment、单次rename，最后原子替换`current.json`。
- Secret泄漏：provider只接受注入配置；公开状态只返回readiness；diagnostic静态且不得携带异常、URL、ID或路径。
- 进程/状态竞争：单非终态run、单Godot进程、审批幂等、内容变更失效；失败保持上一份current。

普通verify仅使用loopback和离线夹具。真实外部调用与远程world删除均需当次人工批准。
