# `@matrix-oasis/runtime-pack-contracts`

R3 的私有、案例无关 Runtime Game Pack、独立 Receipt 与规范 JSON 合同包。两个 JSON Schema 2020-12 文件是结构真源；JavaScript 入口导出深冻结 Schema、固定身份常量和浏览器兼容的规范序列化器。

Runtime Pack 保留声明顺序，以零基安全整数索引表达引用。所有作者侧可选字段在编译产物中实体化为 `null` 或空数组。Receipt 只记录编译器身份及 Artifact 的 SHA-256/字节数；它不参与 Artifact 哈希，也不是签名或信任证明。

`canonicalizeJsonValue(value)` 只接受可无损表达的 JSON 数据：`null`、布尔值、字符串、安全整数、稠密数组和 prototype 为 `Object.prototype` 或 `null` 的普通 record。对象键按 UTF-16 code unit 排序，数组保序，`-0` 写为 `0`，输出无 BOM、空白或结尾换行，也不执行 Unicode 规范化。为避免隐藏执行，它通过一次捕获的属性描述符读取数据，并拒绝 accessor、symbol key、其他自定义 prototype、稀疏数组、循环、非良构 UTF-16 及超过 256 层的输入；不会调用 getter 或 `toJSON`。描述符捕获期间的 Proxy trap 异常统一转换为不携带原异常细节的 `CanonicalJsonOperationalError`。

本包不解析文本、不计算哈希、不验证索引边界，也不执行 Runtime Pack。解析、结构/语义/完整性验证、编译和执行分别由后续独立包负责。

```js
import {
  CANONICAL_JSON_PROFILE,
  RUNTIME_GAME_PACK_SCHEMA,
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
```

格式版本固定为 `0.1.0`，规范 JSON profile 固定为 `matrix-oasis.canonical-json/1`；未来变更必须显式引入新版本，不做隐式兼容。
