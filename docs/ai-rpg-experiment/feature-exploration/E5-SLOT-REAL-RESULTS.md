# E5 提案字段单次补测结果

2026-11-14。工作树C:/tmp/modelmirror-rpg-context-plans-e5；分支codex/rpg-context-plans-e5；基线bfc3d748438637215530f66a50cdd4e4d915f852。用户授权最多1次Gemini、总额≤$0.20、不自动重试，先完整模拟后实测。本次自动核对通过，待用户页面验收；不宣称整个E5的全部真实场景完成。

## 实际结果

完整模拟1次通过后，真实调用1次完成。原始回复使用单一完整JSON围栏，既有parse直接解析；没有字段重命名、删除、自动转换或内容修补。

| 项目 | 原始回复与校验结果 |
|---|---|
| 第1步 | op.action=use_medicine；changes仅item=medicine、quantity=2 |
| 第2步 | op.action=gift；changes仅target=zhou、item=medicine、quantity=1 |
| 条件 | 第2步op.condition，kind=prerequisite，value/quote均逐字为“如果他不收，就不送。” |
| 有序计划 | 两个add通过原有E4/E5校验；step-2依赖step-1，无额外步骤 |
| 规则预检 | 仅第1步基于当前快照计算5→3；不是实际消耗 |
| 赠送 | 没有执行规则或服药预检，条件未核实，未交付 |
| 世界状态 | 完整源记录指纹不变，实际养息丹始终5 |
| 保存 | 脚本保存通过校验的只读计划供下载，executionStatus=not_executed、requiresExecutionValidation=true；不代表执行授权 |

原始回复已归档；有效计划由原回复直接形成，未把前次错误回复转换成合法内容。本次只验证字段、两步与单步条件，连续撤回/数量修订/玩家陈述的原定第二条仍未真实验证。单个样例不能证明任意输入稳定。

## 模型、预算与统计

google/gemini-3.8-flash；既有OpenRouter选定连接，限定Google AI Studio标准端点google-ai-studio，allow_fallbacks=false。temperature=0.7、top_p=0.8、max_tokens=8192、tools=[]、tool_choice=none。派发前冻结资格和当前标准报价：输入$0.75/M，输出/其中推理$3.75/M。

| 指标 | 实际值 |
|---|---:|
| 真实请求 / 重试 | 1 / 0 |
| 输入token | 1065 |
| 输出token | 1195 |
| 其中推理token | 858 |
| 供应商实报费用 | $0.00528000 |
| 预算预留（非实际收费） | $0.03519375 |
| 本轮授权上限 | $0.20 |
| 首次文本（宿主观测） | 12.276秒 |
| 请求完成 | 13.784秒 |
| 计划解析/校验完成 | 13.885秒 |

推理858已包含在输出1195，不再相加。本轮输出为JSON提案，没有叙事正文首字；首次文本由250ms轮询观测。本轮1次授权已使用，不因美元尚有余量追加调用。

## 证据与清理

数据：experiments/ai-rpg-engine/.rpg03-work/e5-slot-real-v1；模拟：e5-slot-mock-v1。包含execution-claim冻结清单/参数/代码指纹、request-1、reply-1、proposal-1与计划差异、saved-plan-1、plan-final、原SSE/回执、源状态前后记录、dispatch-audit、assessment、cleanup、模拟摘要、离线测试与页面截图。原18919/18922失败证据未覆盖。

- 源记录前后指纹：ec441d30df9a498f7cde8e4139ed2c224209ec5659e92f8ed90e4be634972ae5。
- 原回复指纹：914694505d953a01916bc54f8bef0842ec2d0e1cc9d5c81859b6e86b4b219b96（项目hash约定）。
- 模拟及真实各1条派发记录；没有第二条请求或重试。
- 两个临时router凭据目录均已删除；密钥/服务token未明文写盘，必要凭据只以原密文暂存。未修改共享配置。
- 仅E5任务私有slot-* / slot_*入口与文档新增，未更改E5提案合同、原校验、公共核心、E4、共享数据或正式入口。

## 验证

新增预算/协议测试及受影响回归：Node 51项通过（E5 29、E4 22），Python9项通过。完整模拟派发1次通过同一资格、出站保护、预算、私有协议及回执链，MockTransport替代模型生成，不计为真实效果。

```powershell
$e5Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-plans-e5 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
$e4Tests = @(Get-ChildItem experiments/ai-rpg-engine/feature-exploration/context-actions-e4 -Recurse -Filter *.test.mjs | ForEach-Object { $_.FullName })
node --test @e5Tests @e4Tests
C:\anaconda\python.exe -m pytest experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/test_protocol.py experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/test_condition_protocol.py experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/test_slot_protocol.py -q
node experiments/ai-rpg-engine/feature-exploration/context-plans-e5/real/slot-review-check.mjs C:/tmp/mm-r3a-pw/node_modules/playwright
```

页面7项检查通过：桌面1440×1080、窄屏390×844、两步/条件/单步预检显示、原回复下载内容一致、展开/刷新、只读路由及来源限制、无外部请求和新增调用。已查看两张截图；独立只读计划下载可用。

## 页面与边界

本次验收：http://127.0.0.1:18925/ 。上一轮字段失败：http://127.0.0.1:18922/ 。更早条件失败：http://127.0.0.1:18919/ 。离线交互原型：http://127.0.0.1:18916/ 。刷新/下载不生成模型请求；18916用户离线草稿未改。

没有正式执行、部署、共享数据迁移、Commit/Push/PR。本次临时派发进程已退出，留下的18925进程只读展示证据。停止时核对其为本任务slot-review.mjs进程，保留证据；不重跑launch来刷新页面，也不删除数据回退。
