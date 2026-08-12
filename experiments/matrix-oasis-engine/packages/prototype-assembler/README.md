# @matrix-oasis/prototype-assembler

R10 私有、离线、确定性的原型场景组装器。它只消费已经通过 R7–R10 权威验证器的 canonical 文本与本地字节，不访问网络、不读取环境变量、不写文件。

公开入口 `assemblePrototypeScene(request)` 返回 canonical Scene Pack、canonical assembly report 与必须由宿主事务复制的文件引用。内容错误只返回静态 diagnostics；不可恢复故障抛出 `PrototypeAssemblerOperationalError`，固定 code 为 `PROTOTYPE_ASSEMBLER_INTERNAL_ERROR`。

R10 profile 固定最多四个 zone、两个非环境 brief、32 个逻辑 placement、每 zone 八个 placement。Marble collider 替换 R9 Asset Bundle 中仅用于资格验证的 Kenney environment；Kenney 不会进入成功 Scene Pack。
