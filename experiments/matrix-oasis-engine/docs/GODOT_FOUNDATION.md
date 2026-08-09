# Godot 4.6.3 工程底座

R4 固定官方标准版 Godot 4.6.3、GDScript 与 Forward+。Godot 通过仓外 `GODOT_BIN` 提供，仓内不保存引擎、导出模板或平台二进制。

工程只证明：可导入、可解析、可 headless 启动、可用 GdUnit4 测试，并能在人工图形会话输出固定帧证据。稳定 readiness marker 为 `MATRIX_OASIS_R4_GODOT_FOUNDATION_READY`。

R4 不定义 Runtime Pack 适配接口；任何桥接必须留到下一轮，并以冻结 R3 Runtime Pack 为输入权威。
