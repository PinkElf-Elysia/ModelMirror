# `@matrix-oasis/game-pack-validator`

R1 的私有、同步、无 I/O Authoring Game Pack 验证器。它只消费
`@matrix-oasis/game-pack-contracts` 0.1.0 合同，不读取文件、不访问网络、
不执行 action、condition 或 effect，也不包含案例、资产或 Godot 概念。

## API

```js
import {
  validateAuthoringGamePack,
  validateAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-validator";

const valueReport = validateAuthoringGamePack(value);
const textReport = validateAuthoringGamePackJson(jsonText);
```

两个函数均同步且不修改输入，返回固定形状：

```js
{
  reportVersion: 1,
  valid: false,
  diagnostics: [
    {
      phase: "semantic",
      severity: "error",
      code: "PACK_TARGET_REFERENCE_UNKNOWN",
      path: "/nodes/0/actions/0/target/id",
      message: "Action target is not declared in the selected target category."
    }
  ]
}
```

诊断可选 `relatedPath`；文本解析诊断可选一基 `location.line` 与
`location.column`。报告不返回 Pack，不包含输入值、Ajv 原始文案、异常堆栈或
本机路径。内容错误不会抛异常；无法完成的内部错误会抛出稳定的
`AuthoringGamePackOperationalError`，其 `code` 为
`PACK_VALIDATOR_INTERNAL_ERROR`，调用方不得回显异常 message 或 stack。

## 验证阶段

1. 文本入口拒绝注释、尾逗号、空内容、重复键和其他非严格 JSON。
2. Ajv 2020-12 以 strict/all-errors 模式验证权威 Schema，不填默认值、不强制
   类型、不删除字段，也不加载远程 Schema。
3. 语义层检查全包顶层 ID、节点内 action ID、全部分类引用、变量类型、enum
   值、最大 16 层 condition，以及忽略条件后的结构图可达性。

显式循环允许存在，但每个入口可达节点都必须在结构上能到达 ending。测试数据
保持中性，只验证引擎合同，不承载产品样例或题材内容。

## 稳定诊断矩阵

公开诊断 `phase`、`code` 与 Pointer 责任如下；数组内对象始终用索引定位，不把 authored ID 写入路径。

| 阶段 | 稳定 code | Pointer 责任 |
| --- | --- | --- |
| parse | `PACK_JSON_INPUT_TYPE`、`PACK_JSON_SYNTAX` | 根 `""` |
| parse | `PACK_JSON_DUPLICATE_KEY` | 已知安全字段指向第二个键；任意未知键退到父对象 |
| schema | `PACK_SCHEMA_REQUIRED` | 缺失字段位置 |
| schema | `PACK_SCHEMA_UNKNOWN_PROPERTY` | 安全父对象，不回显未知键名 |
| schema | `PACK_SCHEMA_TYPE`、`PACK_SCHEMA_CONST`、`PACK_SCHEMA_ENUM`、`PACK_SCHEMA_SHAPE`、`PACK_SCHEMA_INVALID` | 违规值位置 |
| schema | `PACK_SCHEMA_MIN_ITEMS`、`PACK_SCHEMA_MAX_ITEMS`、`PACK_SCHEMA_DUPLICATE_ITEM` | 数组或重复项位置 |
| schema | `PACK_SCHEMA_STRING_CONSTRAINT`、`PACK_SCHEMA_NUMBER_CONSTRAINT`、`PACK_SCHEMA_FORBIDDEN_VALUE`、`PACK_SCHEMA_NON_JSON_VALUE` | 违规值位置 |
| semantic | `PACK_TOP_LEVEL_ID_DUPLICATE`、`PACK_ACTION_ID_DUPLICATE` | 后续重复 ID，并以 `relatedPath` 指向首次声明 |
| semantic | `PACK_ENTRY_NODE_UNKNOWN`、`PACK_ENTITY_REFERENCE_UNKNOWN`、`PACK_CUE_REFERENCE_UNKNOWN`、`PACK_VARIABLE_REFERENCE_UNKNOWN`、`PACK_TARGET_REFERENCE_UNKNOWN` | 具体引用字段 |
| semantic | `PACK_ENUM_INITIAL_NOT_ALLOWED`、`PACK_ENUM_VALUE_NOT_ALLOWED` | 具体初值或 value |
| semantic | `PACK_CONDITION_VARIABLE_TYPE_MISMATCH`、`PACK_CONDITION_VALUE_TYPE_MISMATCH`、`PACK_EFFECT_VARIABLE_TYPE_MISMATCH`、`PACK_EFFECT_VALUE_TYPE_MISMATCH` | `variableId` 或 `value` |
| semantic | `PACK_CONDITION_DEPTH_EXCEEDED` | 第一个超过 16 层的 condition 对象 |
| semantic | `PACK_NODE_UNREACHABLE`、`PACK_NODE_NO_ENDING_PATH` | 节点 ID 字段 |

同一输入的报告字节稳定。JSON/Schema 失败时不进入 semantic；图诊断只在 ID、入口和 typed target 足以构图时运行。
