# R21 人格种子、记忆与关系派生状态

R21在R19 World Event Ledger与R20已资格单时间线之上增加两个只读派生投影。Runtime仍是游戏状态唯一权威，Ledger仍是裁决历史、因果关系和来源证明唯一权威。Persona Seed和Relationship Policy是可信、版本化输入，不从叙事文案、cue或模型输出推断。

## 产物

- `NPC Persona Seed 0.1.0`：完整覆盖当前R20真实绑定actor的整数trait向量；允许资格时间线没有真实actor，但trait词表仍须闭合。
- `NPC Memory Projection 0.1.0`：只记录actor自身被R19接受的Action，保留revision、entry、Intent、Runtime transition和交互实体来源；拒绝事件不进入普通memory。
- `NPC Relationship Projection Policy/Projection 0.1.0`：只接受显式、定向、精确node/action映射的整数delta；同一规则在单timeline只取第一次accepted匹配，不推断互惠或自由文本关系。
- `NPC Derived State Bundle 0.1.0`：绑定R20 current/manifest/资格证据、R19完整重放、两类reducer源码身份、两类artifact及R19 Derived Projection Manifest。

## 稳定接口和CLI

核心API为`prepareNpcDerivedState`、`projectNpcDerivedState`和`verifyNpcDerivedState`。验证不会信任调用方自报的reducer hash或manifest包装，而是重新完整重放Ledger并由编译期allowlist中的reducer重算全部字节。

CLI提供：

- `project:npc-derived-state`：从精确R20 current生成不可变八文件目录。
- `verify:npc-derived-state -- ...`：复验指定资格目录；无参数时运行R21自动测试门。
- `qualify:r21`：20次重复生成、整体删除探针、重新生成和原子发布资格报告。
- `validate:npc-derived-state`：验证persona、policy、memory、relationship、bundle或qualification合同。

来源读取持有R20单写者lease，读取前后复验FileHandle、realpath、`dev:ino`、mtime/ctime与字节身份。R21只让R20审计器看到具有完整`qualification-evidence.json`的时间线链，因此无关的历史未完成时间线不会阻断当前资格；`npc-current`本身、已资格链、待激活后继、分叉或任何当前文件损坏仍然fail closed。

## 删除语义与限制

Windows上的资格删除采用精确身份验证后的同父原子rename，把R21自建目录移出成功命名空间并隔离在不可消费的隐藏quarantine中。它证明“原资格路径消失、第二次重建不复用旧目录、字节完全一致”，但不是安全字节擦除；标准Node文件API不能在防御同用户父目录换身的同时提供delete-by-handle。选择性遗忘、单条更正、跨timeline合并、语义检索、embedding、外部数据库和自动人格演化均不属于R21。

## 回退

停用`matrix-oasis.npc-derived-state/1`并删除仓外派生目录即可回到R20。逆序revert R21七个提交不会修改R19 Ledger、R20时间线、R16 Creator默认入口或供应商资产。
