# Godot 4.6.3 工程底座

R4 固定官方标准版 Godot 4.6.3、GDScript 与 Forward+。Godot 通过仓外 `GODOT_BIN` 提供，仓内不保存引擎、导出模板或平台二进制。

当前核验引擎标识为 `4.6.3.stable.official.7d41c59c4`；Windows 标准版官方 SHA-512 为 `d44ea7ef5bab754cacd49d581b6062836b2eea12a82e1d183aebfad9cd8c7db2bd82513337bd657d6d2d5c04d46239c0570b029faf1343e81e8a2fa7b85dd83b`。二进制及校验清单仅保存在仓外工具缓存。

工程只证明：可导入、可解析、可 headless 启动、可用 GdUnit4 测试，并能在人工图形会话输出固定帧证据。稳定 readiness marker 为 `MATRIX_OASIS_R4_GODOT_FOUNDATION_READY`。

Godot 4.6 自动生成的 `.gd.uid` 是 GDScript 源码身份 sidecar，和源码一起跟踪；`.godot/` 导入缓存仍始终忽略。

自动 import、GdUnit 和 smoke 均先把工程复制到仓外一次性目录再启动 Godot，成功后只清理该精确目录。这样 Godot 为上游 addon 生成的派生 UID 和导入缓存不会改变原样 vendored 树；失败副本保留供诊断。

R4 不定义 Runtime Pack 适配接口；任何桥接必须留到下一轮，并以冻结 R3 Runtime Pack 为输入权威。
