# RPG-04 代表上下文卡包

04B2b 将 RPG-02 已验证代表编译结果与 `fixtures/rpg04/authored-resources.json` 合并为独立的 `card.rpg04-representative/0.1.0`。builder 接收显式编译结果和 authored 原始文本；文件读取仅存在于固定 fixture wrapper。原资源、来源与权利记录按原顺序保留，新补写资源追加独立 authored source 与 authorization right。

author source 使用精确文件字节 SHA-256 `b395447f2cb1ce80763fb470b49b5801df110cfc8462d16e09678f9b8d4f8692`。主持模板使用 `SHA-256 UTF-8 canonicalJson(HOST_TEMPLATE)`，固定为 `07a4b6987abde92afec3f43ec13fe18d2cf4d7a58dc4d819b0c73918641752bb`。输出回执另含 canonical card hash，供后续 profile 和 prepared-turn 绑定；本批未实现 profile、选择器或编排器。

封闭 authored 信封复用 `CARD_PACKAGE_SCHEMA` 的 styles、openings、worldbookEntries、informationModules 与 defaults 组件。受信 builder 先校验固定文件字节 hash，再以 `parseStrictJson` 解析并做封闭 schema 校验；合并后执行完整卡包和玩家绑定语义校验。独立 source validator 仅返回验证报告，用于离线验证不同字节的结构与引用反例，不能产出受信卡包。诊断稳定且不回显源文本、绝对路径或堆栈。

虚拟玩家仅更新卡包引用和新默认开场，persona、五项 talent、五项 activation 与空 `runtimePermissions` 保持不变。该卡仍为零可选插件。结构与离线门禁不构成主持行为或真实模型验收。

后续批次前的已知阻塞：现有 context condition 合同会拒绝两类本来有效的冻结卡状态字段。一是 `shortText` 的空字符串初始值会被 condition schema 的 `minLength: 1` 拒绝；二是未声明 minimum/maximum 的 integer 字段会在条件值比较时与 `undefined` 比较并被拒绝。这两项已由主任务独立复现，不在 04B2b 修改范围，必须在进入 04C1 前以单独有界批次修复并回归。
