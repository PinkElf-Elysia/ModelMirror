# `@matrix-oasis/prototype-creator-qualification`

R16 私有的 Creator 资格事务缓存。它只发布通过严格合同和外部引用复验的资格清单；不会复制旧 source、solution 或 evidence 缓存，也不会把这些缓存单独解释为 Creator `ready`。

公开接口：

- `publishQualifiedCreatorRun(request)`：计算 canonical 资格清单 SHA-256 作为 run ID，在同父 staging 中写入并复验，最后原子替换 `qualified-current.json`。
- `loadVerifiedQualifiedCreatorRun(request)`：重新校验清单、run ID 和所有注入引用。
- `findVerifiedQualifiedCreatorRun(request)`：优先返回匹配的有效 current，否则按 run ID 稳定选择。
- `recoverQualifiedCreatorRuns(request)`：忽略不合格历史，且只在 current 指向有效 run 时恢复 current。

所有入口都强制注入 `verifyReferences`。该回调必须返回 `true` 或 `{ valid: true }`；缺失、抛错或否定结果均 fail closed。运行根必须是指定临时根的直接子目录，符号链接、junction、路径换身、已有目标和并发发布冲突都被拒绝。
