# Godot Runtime Pack 适配合同

## 输入

- `--matrix-oasis-runtime-pack=<path>`：规范 Runtime Pack 0.1.0，最大 16 MiB。
- `--matrix-oasis-runtime-receipt=<path>`：对应规范 Receipt 0.1.0，最大 16 KiB。
- 两项必须同时存在且只读；不接受目录、网络 URL、环境变量或隐式默认路径。

## 稳定输出

- 载入并创建会话后精确输出一次 `MATRIX_OASIS_R5_GODOT_RUNTIME_READY`。
- trace 模式用 `MATRIX_OASIS_R5_TRACE_JSON:` 前缀输出单行 JSON；前缀前后不得夹带输入内容。
- diagnostics 固定字段为 `phase`、`severity`、`code`、`path`、`message`，其中 message 等于静态 code。

载入失败 code 固定为：`GODOT_RUNTIME_INPUT_INVALID`、`GODOT_RUNTIME_JSON_INVALID`、`GODOT_RUNTIME_SCHEMA_INVALID`、`GODOT_RUNTIME_SEMANTIC_INVALID`、`GODOT_RUNTIME_INTEGRITY_MISMATCH`、`GODOT_RUNTIME_UNSUPPORTED_TEXT`。执行失败沿用冻结 R3 的 `PACK_RUNTIME_*` code；内部故障固定为 `PACK_GODOT_RUNTIME_INTERNAL_ERROR`。

## 非接口

prepared handle、GDScript Dictionary、trace JSON 和会话快照都不是正式存档或跨版本 ABI。R5 不保证调用方修改返回 Dictionary 后继续使用；每个公开操作必须重新验证并复制可观察输入，且不得暴露 prepared 内部 Pack。
