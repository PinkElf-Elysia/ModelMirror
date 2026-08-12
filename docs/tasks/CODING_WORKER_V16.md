# V16 OpenCode 等效门禁与受控多 Agent 任务卡

## 基线与宣传边界

- 开工基线：合并 PR #173 后的 `origin/main@dce62ee54c033f1b0b55f69f807c7c6b4bb91df5`。
- PR A 只实现真实会话与等效 Harness；PR B 的单 Agent 能力补齐和 PR C 的 Subagent 均不提前混入。
- 在两轮完整真实对照通过前，功能保持 Experimental、默认关闭，禁止使用“接近原版 OpenCode”“等同 OpenCode”或“完整替代 OpenCode”。
- 最终唯一允许的表述是：“在已验收的受控 Python/TypeScript 仓库开发任务上，ModelMirror Coding Worker 的任务成功率与恢复能力接近 OpenCode 1.18.9。”

## PR A 文件与提交边界

每个逻辑提交不超过五个文件：

1. 冻结 24 项任务清单、隐藏检查摘要、144 格运行矩阵、确定性报告判定和两轮认证。
2. Provider 私有契约 v3：统一 plan、todo、工具边界、usage、question、compaction 与错误分类。
3. 规范化会话台账：只保存公开消息、计划、todo、工具摘要、operation ID、检查证据和完整边界，不保存隐藏思维链。
4. 活动预算：只累计 preparing/running/testing，跨重启不重置；queue、approval、user-input 等待不计入。

## 冻结对照规则

- 任务清单固定为 Python、TypeScript/React、仓库开发、会话与协作四类，每类六项，每项对两侧各运行三次。
- 两侧使用相同的受控模型路由回执、目标、初始 tree、隐藏检查、时间、轮次和输出预算。
- timeout、budget limited、卡死、人工修复、未声明副作用、策略违规或错误最终 tree 一律计为失败。
- CI 只验证清单、结果模式、判定算法和 Fake runner；真实模型对照只在人工发布窗口运行。
- 报告只保存公开输出和统计，不保存密钥、隐藏检查正文、供应商原始帧或隐藏思维链。

## 量化门禁

- Worker accepted-run 成功率至少 85%，且落后 OpenCode 1.18.9 不超过 5 个百分点。
- 每个类别至少 80%；OpenCode 达到 2/3 的任务，Worker 不得 0/3。
- 安全、原子性、跨任务隔离和重启唯一性用例必须 100%。
- Worker 中位活动时长与 token 均不超过基线 1.5 倍；批准等待时间不计入活动时长。
- 同一 candidate SHA 和冻结 manifest 必须连续两轮完整通过。
- 发布窗口同时记录最新 OpenCode 版本的差距审计；固定 1.18.9 不能用于隐藏新增能力差距。

## 自动与人工证据分离

自动门禁记录命令、计数和环境；Mock/Fake 只证明确定性协议，不证明真实模型成功率。人工窗口需保存版本、candidate SHA、manifest hash、tree hash、检查结果、tokens、工具调用、活动时长和失败分类，并在 Console 脚本化验收无 P0/P1 后才能决定是否采用限定宣传文案。

## 回退

关闭 V16 对应开关后，新任务回到 V15；已有 V16 任务进入 `interrupted`，保留 Store、Workspace、Evidence 与 v13 Recovery。不得自动重放工具副作用或删除用户数据。
