# R13 空间生成参考

本目录只保存固定上游来源的非执行适配笔记、许可证和机器锁。它不是 vendored 运行时：

- 不包含 Python、C#、GDScript 或可导入模块；
- 不加入 Godot、Node 或 Creator 的运行依赖；
- 不复制上游求解器；
- 只把可验证的设计模式转换为 R13 合同、分析器测试和证据门。

`reference.lock.json` 固定 commit、上游 Git blob、完整源文件 SHA-256、许可证身份和本地笔记字节。`npm run verify:spatial-references` 会拒绝未知文件、链接、可执行扩展或字节漂移。
