# `@matrix-oasis/prototype-creator-qualification`

R16 私有的 Creator 资格事务缓存。它只发布通过严格合同和外部引用复验的资格清单；不会复制旧 source、solution 或 evidence 缓存，也不会把这些缓存单独解释为 Creator `ready`。

公开接口：

- `publishQualifiedCreatorRun(request)`：计算 canonical 资格清单 SHA-256 作为 run ID，在同父 staging 中写入并复验，最后原子替换 `qualified-current.json`。
- `loadVerifiedQualifiedCreatorRun(request)`：重新校验清单、run ID 和所有注入引用。
- `findVerifiedQualifiedCreatorRun(request)`：优先返回匹配的有效 current，否则按 run ID 稳定选择。
- `recoverQualifiedCreatorRuns(request)`：忽略不合格历史，且只在 current 指向有效 run 时恢复 current。

所有入口都强制注入 `verifyReferences`。该回调必须返回 `true` 或 `{ valid: true }`；缺失、抛错或否定结果均 fail closed。运行根必须是指定临时根的直接子目录，符号链接、junction、路径换身、已有目标和并发发布冲突都被拒绝。

`qualifyPrototypeForCreator(request, operations)` 提供不含文件或网络能力的纯编排层。它按缓存级别执行最小续跑：

- `qualified`：只调用 `verifyQualified` 复验全部绑定引用；
- `evidence-only`：调用 `verifyEvidence` 后发布资格，不重新取证；
- `solved-only`：复验 solved 缓存后只运行 `collectEvidence` 并发布；
- `source-only`：依次执行 `analyze`、`solve`、`verify`、`collectEvidence` 和发布。

每一步能力都由宿主显式注入。编排器用显式 `expectedSolutionSha256` 阻止旧 evidence 与不同 Spatial Solution 混用；未显式锁定时，允许 R15 候选排除产生新的最终 Solution，但要求最终 solved、verification 与 evidence 身份完全一致。`onStage({ stage: "qualifying", subphase, attempt })` 提供 `analyzing → solving → verifying → evidencing` 进度。任一先决条件失败都返回静态诊断且不会调用发布操作。
