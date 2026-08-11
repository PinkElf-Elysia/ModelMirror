# 模块边界

## R8允许范围

- 只修改 `experiments/matrix-oasis-engine/**`。
- R1–R7 apps、examples、既有packages、Godot、资产、vendor和历史验收全部冻结。
- 新代码只允许进入两个R8私有workspace、精确CLI/harness文件和R8文档。
- Creator、Godot、父路由、父API、Docker和共享栈不变。

## 输入、网络与输出

- 输入只允许最大32 KiB、fatal UTF-8纯文本；不读取图片、视频、全景、3D文件或父仓数据。
- 只有 `packages/prototype-generator/src/openai-compatible.mjs` 可以发起模型请求；它不读取环境变量。
- CLI可以读取三个R8专用模型环境变量，但不得打印或持久化其值，也不得读取父仓模型变量。
- endpoint只允许HTTPS，或用于自动测试的loopback HTTP；禁止redirect、stream、tools和自动网络重试。
- 每次生成最多3个请求：1次初始生成和2次定向修复。
- 生成物只能事务发布到 `C:\tmp` 下的新目录；不得跟踪真实模型输出或详细响应日志。

## 初版防偏离门

R8只消除“人工编写结构化原型”的步骤。图片输入、资产生成、Marble、Meshy、NPC、记忆、任务规划、世界事件、运行期AI和Godot启动均不属于本轮。

## 自动范围与回退

- schema v8固定 `activeRound=R8` 和基线 `21cbbb8b943b6f9d9799f014c44a6349e6124a63`。
- 精确allowlist优先于广义冻结根；未明确放行的旧路径和新路径全部失败关闭。
- 普通verify只使用loopback假Provider，不产生费用。
- 每批逆序revert；整体回退恢复完整R7，不涉及数据库、服务或运行数据。
