# 变量面板 A 组真实验收：执行开关阻断

本轮已获用户授权：4 次剧情，JP Gemini 3.8 Flash，temperature=0.70、top_p=0.80、max_tokens=8192，M1=1 与分支；M2/M3 关闭。不自动重试或认证。原冻结方案见 `VARIABLE-PANEL-LIVE-PLAN.md`。

## 实际结果

- 创作 UI 18648 从冻结作品包建立独立作品、版本和会话，经 UI 发送 V1 一次。
- 控制面返回 HTTP 503：`rpg_authoring_execution_disabled`。运行容器的 `RPG_AUTHORING_EXECUTION_ENABLED=false`。
- 按原会话/请求 ID/hash 查询得到 HTTP 200、`status=absent`。已核实运行代码在创建原生任务记录、供应商调用之前检查此开关。
- JP 路由上限由用户批准从 6 增至 10，已用仍为 6（revision=3）。本轮供应商派发 0 次、认证 0 次、完成回合 0。
- 本地已记录一次向控制面提交，因通用非 2xx 处理记作 `unknown`，占住 1/4；原操作恢复查询 `absent` 不会自动终结该状态。UI 因而显示“剩余 3 次／结果待核对”。没有手动清账、补造回执或抹除记录。
- V2–V4 均未派发。V1 也没有自动重发。本轮本机中继已关闭后续 POST 派发，仍允许原 ID 查询。

## 定位与待修复边界

1. 目录资格和参数校验不包含共享执行开关；本次预检漏查该项，不能把 available=true 视为可执行证明。
2. 共享 JP 后端需显式开启创作执行能力。此项涉及共享配置更新及可能的重启，尚未执行；不得借用本次生成授权静默部署。
3. 对明确未派发的拒绝，应补齐可验证终态、持久记录和本地额度结算；真正未知结果仍保留原 ID，不得仅凭 absent 自动重发。
4. 本次原请求需根据持久的 503、原生 absent、执行顺序及两层账本核对结果明确收束；修复后离线验证，再获明确恢复许可后在原 4 次生成授权内继续。

## 证据与保留

会话：`session-828a4d6b263c92e774af02ba17c76b27e078c7761fdae60e5183d20ee1e8816e`。
操作：`95f757ee-3304-43ba-9d23-862494a034fa`。
请求：`request-b080136c4b1f3d22673c65cfced1e31d8d581d8053cbc9330fab4019ca3623b8`。

本地不纳入 Git 的目录：`experiments/ai-rpg-engine/.rpg04-work/variable-panel-live-A-20261105/`。保留 `authorization.json`、`allocation.json`、`V1.armed.json`、`V1.claim.json`、`V1.request.json`、`V1.returned.json`、`blocked.json`、`closed.json`、会话/transport/额度记录和截图。

39 个已验收修改文件的 hash 均未变化；18642、18644、18646、18648 入口均返回 200。此次仅新增方案/阻断文档与本地验收证据，无运行源码修改，无提交、Push、PR、共享部署。真实验收未通过，离线结果不替代真实模型验证。

回退：保留已关闭派发的 18648 和原操作供核对；18642、18644 保持原样。没有删除或重置任何账本、作品、会话或凭据。
