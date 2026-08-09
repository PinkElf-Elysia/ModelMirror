# Runtime Game Pack 0.1.0

状态：R3.2 合同与严格 Validator。Compiler、独立 Runtime Simulator、parity harness 和 Creator 双执行接入仍未实现。

## 目的

Runtime Game Pack 是 Authoring Game Pack 0.1.0 的可读、不可变、规范 JSON 表示。它保留全部可观察内容和声明顺序，只把运行时引用转换为零起始安全整数索引，并把批准的可选字段实体化。它不是 Godot 场景、字节码、优化产物或正式存档。

格式标识：

- Pack：`matrix-oasis.runtime-game-pack` / `0.1.0`
- Receipt：`matrix-oasis.runtime-game-pack-receipt` / `0.1.0`
- 规范化：`matrix-oasis.canonical-json/1`
- 合同包：`@matrix-oasis/runtime-pack-contracts@0.1.0-r3`
- 验证包：`@matrix-oasis/runtime-pack-validator@0.1.0-r3`

## Pack 形状

根对象固定包含：

```text
format / formatVersion / canonicalization
source
language / title / summary
entryNodeIndex
entities / variables / cues / nodes / endings
```

`source` 固定包含 Authoring `format`、`formatVersion`、`id`、`contentVersion` 与完整规范化 Authoring 内容的 `canonicalSha256`。R3.2 Validator 没有 Authoring 输入，因此只验证该哈希的格式，不宣称能证明其来源；R3.3 Compiler 才负责生成它。

映射规则：

| Authoring | Runtime |
| --- | --- |
| `entryNodeId` | `entryNodeIndex` |
| `entityIds` | `entityIndexes` |
| `entryCueIds` / ending `cueIds` | `entryCueIndexes` / `cueIndexes` |
| condition/effect `variableId` | `variableIndex` |
| `emitCue.cueId` | `cueIndex` |
| target `{kind,id}` | target `{kind,index}` |
| 缺失 `summary/description/text/when` | 明确 `null` |
| 缺失 action `entityIds` | 明确 `[]` |

所有对象闭合，全部数组保持 Authoring 声明顺序。Runtime 仍保留实体、变量、Cue、节点、结局和 action 的稳定 ID，以及 title、label、description、text、intent 等展示信息。禁止内嵌原 Authoring Pack、保留 ID 引用字段、数组重排、死代码删除、常量折叠、压缩、二进制或 Godot 专属字段。

索引必须是 `0..Number.MAX_SAFE_INTEGER` 的整数，并在语义验证阶段落入正确类别数组范围。节点最多 4096 个；每个节点最多 64 个 action；每个 action 最多 32 个 effect。condition 根深度为 1，最大 16；显式循环允许，但入口必须有效、所有节点静态可达且每个可达节点存在一条静态 ending 路径。

## canonical-json/1

`canonicalizeJsonValue(value)` 的输出规则：

- 对象键以 JavaScript UTF-16 code unit 比较排序，不使用 `localeCompare`。
- 数组严格保序。
- 只接受 `null`、boolean、well-formed UTF-16 string、安全整数、稠密数组与普通 record。
- `-0` 输出为 `0`；不接受小数、NaN、Infinity、unsafe integer、BigInt、undefined、函数或 symbol。
- 使用 ECMAScript JSON 字符串转义；不做 NFC/NFD 或其他 Unicode normalization。
- UTF-8、无 BOM、无缩进、无多余空白、无尾随换行。
- 最大值深度为 256；循环、accessor、隐藏字段、symbol key、自定义原型和稀疏数组拒绝。

序列化器通过 property descriptor 捕获普通对象，不读取 getter 或调用 `toJSON`。JavaScript 无法可靠识别所有透明 Proxy；实现不承诺拒绝所有 Proxy，但 trap 异常只会变成静态 `CANONICAL_JSON_INTERNAL_ERROR`，不会回显底层异常。

## Receipt 与完整性

Receipt 是独立规范 JSON：

```text
format / formatVersion / canonicalization
compiler: { id, version }
artifact: { format, formatVersion, sha256, byteLength }
```

R3.2 固定 Compiler 身份为 `@matrix-oasis/game-pack-compiler@0.1.0-r3`。`artifact.sha256` 覆盖 Runtime Pack 最终规范 UTF-8 字节；`byteLength` 也是 UTF-8 字节数。Receipt 不进入 Artifact 哈希，避免自引用。

Receipt 只证明给定 Pack 字节与回执一致，不是签名、作者身份、可信编译器证明或防恶意重签机制。拿到 Pack 的一方可以同时伪造新的 Receipt；签名、密钥管理和分发信任不属于 R3。

## Validator

公开入口只有：

```ts
validateRuntimeGamePackJson(runtimeText, receiptText): Promise<ValidationReport>
```

它不读文件、不返回解析后的 Pack/Receipt/哈希，也不执行游戏行为。验证按全局门执行：

1. `parse`：两个文档的严格 JSON、重复键、输入类型和最大原始嵌套深度 256；
2. `schema`：闭合 JSON Schema 2020-12、类型、常量和范围；
3. `semantic`：ID、typed index、enum、condition/effect 类型、深度与图；
4. `integrity`：两份规范文本、Runtime UTF-8 byteLength 与 SHA-256。

任一阶段失败都会阻断后续阶段。诊断根路径固定为 `/runtimePack` 或 `/receipt`；消息为静态 code，不回显输入值、未知键名、ID、哈希、文件路径、Ajv 参数、底层异常或堆栈。内容拒绝返回冻结报告；不可恢复的环境/实现故障只抛固定 `RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR`。

Validator 运行源码保持浏览器兼容，哈希使用 Web Crypto；无 `node:*`、文件系统、网络、环境变量或持久化访问。

## 兼容与回退

- Validator 只接受上述两个 `0.1.0` 格式和 `matrix-oasis.canonical-json/1`。
- 未知格式、版本、字段或编译器身份失败关闭，不猜测、不迁移。
- 改变既有合法 Runtime Pack 字节或语义时必须升级 Runtime `formatVersion`。
- R3.2 可通过逆序 revert 本批提交删除两个新 workspace 和根接线；冻结的 R1/R2 输入不受影响。
