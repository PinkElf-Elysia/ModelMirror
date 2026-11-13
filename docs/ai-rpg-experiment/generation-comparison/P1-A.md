# P1-A：类型化参数能力与受控请求

状态：P1-A 已实现并通过离线验收，等待用户确认本批；P1-B 尚未开始。P0 已由用户验收。

## 开工合同

- 分支 codex/rpg-generation-comparison，基线 d9f20fe691e8ddef8623d779a25a9359717f2e8d。
- 开工状态只有本轮 P0 五文件未跟踪，保留其原文与 18660 入口；原主工作区及并行 UI 不动。
- 单一目标：能力 v2 → 默认/显式参数校验 → 受控任务冻结 → 供应商 payload → 回执，离线闭环。
- 允许六文件：server/model_router/rpg_parameters.py（新增）、provider_catalog.py、rpg_bridge.py、rpg_authoring_execution.py；server/tests/test_rpg_parameters_v2.py（新增）；本文。
- 六文件原因：目录声明、预检及执行必须同批升级并有独立测试，避免只展示高级参数而发送阶段不支持。新纯校验模块避免将复杂规则继续堆入桥接路由。
- 不做：P1-B 三用途会话持久配置/预设接口、P2 UI/六卡接线、对比协调、真实认证/调用、共享部署、Git 提交发布。
- 无生产依赖、无数据库迁移；只增加显式 v2 协议和新任务记录，旧 v1 请求与已落盘记录保持兼容。未启用新协议不改变原发送行为。
- 风险：中；修改受控 Provider 请求边界。前端不传供应商字段名、地址或凭据；原资格、预算、同源及 no-replay 门禁保留。

## 证据与实现规则

开工时已证实：provider_catalog.py 的 v1 仅含三数字字段；rpg_bridge.py 预检直接将 wireName 映射；rpg_authoring_execution.py 直接读取 parameters.maxOutputTokens。已有任务落盘和 MockTransport 验收可复用。

v2 使用有界类型声明：number、integer、boolean、enum、text-list。声明明确默认为省略或受信值，配置明确 default/explicit；未知字段、版本、范围、类型、互斥、依赖、能力漂移均拒绝。条件使用封闭的 set/unset/eq 与字段大小比较，不运行表达式。

能力按模型与路由绑定，公开投影不暴露 wireName；控制面私有声明只允许既定参数映射。maxOutputTokens 必须能解析为具体正整数后才派发。v1 的可选 defaultValue 仍是界面建议，不转换成自动派发值。

高级支持必须有明确声明；本批不从模型名称或原型样例推断模型能力。旧 OpenRouter 三参数发现逻辑保持；仅当存在合法 v2 声明时接纳高级能力。能力未知继续阻断。

官方资料（2026-11-05，经 Context7 与官方页面核对）：
- https://openrouter.ai/docs/api_reference/parameters：默认省略与显式传值不同；停止词、Seed 与采样字段。
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens：reasoning.enabled / effort / max_tokens；effort 与预算不能同时设置。

这些是字段映射依据，不是某一实际模型的资格或能力认证。

## 验收与回退

- 基线/回归：python -m pytest server/tests/test_rpg_bridge.py server/tests/test_rpg_authoring_execution.py server/tests/test_provider_catalog_service.py -q
- 新验收：python -m pytest server/tests/test_rpg_parameters_v2.py -q
- 测试只用 MockTransport、合成资格与独立临时目录；捕获真实执行函数构造的请求，验证默认省略、显式值、嵌套思考、三用途任务、容量、漂移、重复操作、未知恢复及 v1 兼容。
- 语法、最终差异、敏感内容扫描；不做不相关前端构建。
- 回退：不启用 v2 客户端；撤回本批源码变更即可，不改旧数据。不删除 v2 操作记录或重放未知结果。

## 结果

最终同一源码状态统一执行：**227 通过，0 失败，4 条既有 FastAPI on_event 弃用警告**，用时 242.56 秒。其中新增 v2 验收 91 项，旧目录/桥接/执行/独立路由回归 136 项。

```powershell
& 'C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe' -B -m pytest server/tests/test_rpg_parameters_v2.py server/tests/test_rpg_bridge.py server/tests/test_rpg_authoring_execution.py server/tests/test_provider_catalog_service.py server/tests/test_rpg_authoring_route.py -q --tb=short -p no:cacheprovider --basetemp C:/tmp/modelmirror-rpg-p1-final-all --junitxml C:/tmp/modelmirror-rpg-p1-evidence/offline-tests.xml
```

- Python 语法编译：五个变更源码/测试文件通过，编译缓存仅放在独立临时目录。
- git diff --check、六文件行末空白及常见敏感内容扫描：通过；暂存区为空，未修改依赖锁文件。
- 初次基线为 120 通过 / 3 失败，原因是默认 Python 缺 duckdb、独立 Node 工作区缺已锁定的 Handlebars；按 lockfile 安装既定依赖并使用已有完整 Python 环境后，三项重跑全部通过。没有修改测试断言或生产依赖来规避失败。
- 原型入口 18660 最终只读检查返回 200；旧入口及共享栈未重启。
- 不运行前端构建或新的浏览器验收：本批不改前端。真实认证、模型调用与共享部署均未执行。

证据目录：`C:/tmp/modelmirror-rpg-p1-evidence`，含 offline-tests.log/xml、四种用途的 request-*.json，以及源码/证据 SHA-256 清单 P1-A-HASHES.json。请求 JSON 是受控执行函数经 MockTransport 捕获的完整 payload 和回执，全部为合成内容；不纳入版本控制。

这里的 227 项通过是离线集成证据，不代表六卡 UI 已接线、会话预设已落盘或真实供应商已验证。

## 已实现的协议

保持路径不变，必须显式选择版本：

| 操作 | v1 | v2 |
| --- | --- | --- |
| 目录 | GET /api/rpg/v1/authoring/models | 同路径增加 parameterVersion=2，返回 control-model-catalog/2 |
| 预检 | 原四字段请求不变 | format=parameter-check-request/2，增加 purpose、capabilityRevision |
| 执行 | authoring-task/1，旧三参数结构 | authoring-task/2，参数配置结构如下，增加 capabilityRevision |
| 恢复 | 原会话/请求 ID 查询 | 同一个持久记录查询路径；新应用进程也不重派 |

上表 format 的完整前缀是 `modelmirror.rpg.`。它们仍是受认证的服务间接口，不开放浏览器直连、凭据或任意供应商字段。

```json
{
  "format": "modelmirror.rpg.parameter-config/2",
  "values": {
    "temperature": {"mode": "explicit", "value": 0.7},
    "maxOutputTokens": {"mode": "default"},
    "stop": {"mode": "explicit", "value": ["结束", "\n\n"]},
    "thinking.enabled": {"mode": "explicit", "value": true},
    "thinking.budgetTokens": {"mode": "explicit", "value": 1000}
  }
}
```

这是合成验收模型的配置示例，不表示任意 Gemini/Opus/DeepSeek 都支持这些字段。未列出的已声明字段使用 default；default 解析为受信声明值或省略。required 且没有受信默认时明确阻断。

- 公共目录不返回 wireName；能力 revision 绑定具体选择、路由/资格及私有声明。
- 预检返回解析快照与 hash；执行时重新核对并冻结，发送前再次校验选择 revision。
- v2 回执增加 parameterResolution、capabilityRevision、providerRequestHash。原请求 hash 区分 default 与 explicit，即使两者最终 payload 值相同。
- summary/compression 使用相同参数用途，任务 purpose 及操作记录仍分别保留；本批不混用或代存三个会话配置。
- 接收数值、整数、布尔、有限枚举、文本列表。Seed 限制在 JS 安全整数内；停止词不修剪、不转义改写，按 Unicode 字符计数，最多 16 项、每项最多 1024 字符且可由模型声明进一步收紧。
- 规则只有 set/unset/eq、互斥组及小于另一数值字段，不执行源码，不自动修复冲突。reasoning.effort 和 reasoning.max_tokens 不同时传入。
- 供应商参数映射只允许内置名单，例如 topP→top_p、thinking.budgetTokens→reasoning.max_tokens；不能通过能力声明加入 api_key、provider、URL 或任意字段。
- 旧 v1 声明可提升为 v2；可选字段的旧 defaultValue 只作为既有界面建议，不被擅自派发。v2 专有声明对 v1 客户端保持不可用，避免删去高级约束后降级发送。
- 解析失败、用途不符、能力漂移、容量不足均无供应商派发；任务拒绝保留明确的未派发失败记录，不误记为未知。

## 本批未覆盖

- 三用途的会话配置修订、预设 CRUD/往返、作品推荐配置与相应宿主接口：P1 后续批。
- 公开模型目录缺少可信细粒度能力时，不自动制造高级范围。真实模型高级能力来源仍需按路由证据接入和验证，本批没有修改共享目录或进行认证。
- 创作与聊天 UI、插件授权和六卡宿主：P2。
- 真实供应商参数接纳和内容效果：另行授权的真实验收；MockTransport 捕获只证明本实现构造并派发了指定 payload。
