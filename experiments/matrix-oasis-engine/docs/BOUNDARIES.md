# 模块边界

## R9允许范围

- 只修改 `experiments/matrix-oasis-engine/**`。
- R1–R8 apps、examples、既有packages、Godot、资产、vendor和历史验收全部冻结。
- 新代码只允许进入两个R9私有workspace、精确CLI/harness文件和R9文档。
- Creator、Godot、父路由、父API、Docker和共享栈不变。

## 输入、网络与输出

- R8 Blueprint只从仓外资格目录读取并按canonical/hash/身份复验；不读取父仓数据。
- 只有冻结的R8 provider与 `packages/prototype-asset-pipeline/src/meshy-provider.mjs` 可以使用受控 `fetch`；后者不读取环境变量。
- 只有资格CLI可以读取 `MATRIX_OASIS_MESHY_API_KEY`，不得打印、写入报告或持久化其值。
- Meshy endpoint固定HTTPS且禁止redirect、SSE和自动重建任务；下载只接受 `assets.meshy.ai` 或测试loopback。
- 真实create、poll、download按任务和阶段分别审批。普通verify不得调用供应商。
- 原始资产和供应商证据留在 `C:\tmp`；最终Asset Bundle事务发布到仓外新目录，不覆盖已有目标。
- Asset Bundle只包含 `assets/*.glb`、canonical manifest和脱敏报告；不得包含任务ID、URL、密钥、原始响应或用户提示。

## 初版防偏离门

R9只消除“Blueprint中的道具/静态人物没有真实资产”的步骤。环境生成、Marble、自动布局、Creator一键预览、NPC、记忆、任务规划、世界事件和运行期AI均不属于本轮。

## 自动范围与回退

- schema v9固定 `activeRound=R9` 和基线 `da5fd0fe39234807ae3c4a1d543b9fd64de66d97`。
- 精确allowlist优先于广义冻结根；未明确放行的旧路径和新路径全部失败关闭。
- 普通verify只使用loopback假Provider与离线GLB夹具，不产生费用。
- 每批逆序revert；整体回退恢复完整R8，不涉及数据库、服务或运行数据。仓外供应商任务和资产需按资格清单另行处理。
