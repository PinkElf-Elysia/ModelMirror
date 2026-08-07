# `@matrix-oasis/game-pack-simulator`

R2 的私有、同步、浏览器兼容参考模拟器。它只消费通过 R1 Validator 的 Authoring Game Pack 0.1.0，以纯内存、单步、确定性的方式提供语义权威；它不是 Compiler、Runtime Pack、正式存档或生产运行时。

## 公共接口

```js
import {
  applyGameSessionAction,
  createGameSession,
  inspectGameSession,
  prepareAuthoringGamePack,
  prepareAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-simulator";
```

`prepare` 成功后返回 opaque、冻结且不可序列化为 Pack 数据的 handle；非法内容返回 R1 验证报告。内部不可恢复错误抛出 `GamePackSimulatorOperationalError`，稳定 code 为 `PACK_RUNTIME_INTERNAL_ERROR`。

会话 API 不修改 Pack、prepared 或输入 snapshot。成功 action 返回新 snapshot、inspection 与本步 transition；预期运行失败只返回静态、无输入值的 runtime diagnostics。

## 固定语义

- snapshot 版本为 1；默认 step limit 为 256，允许范围 1..10000。
- condition 读取 action 前状态并左到右短路。
- effects 按声明顺序读取前序 effect 的工作副本，最后才迁移。
- 任一 `add` 产生非安全整数时整步回滚，Cue、变量、位置与 step 均不提交。
- 本步 effect Cue 先于目标 node entry Cue 或 ending Cue；重复 Cue 保留。
- ending、未知或不可用 action、非法 snapshot、Pack 不匹配与步数上限使用稳定 runtime diagnostics。
- 快照可 JSON round-trip 后继续交给同一 prepared handle，但不是正式存档格式。

本包不读取文件、环境或时钟，不访问网络，不使用随机数，不持久化状态，也不包含任何样例题材分支。
