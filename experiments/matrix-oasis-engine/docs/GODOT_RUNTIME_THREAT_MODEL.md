# R5 Godot Runtime 威胁模型

## 不可信输入

本地路径、原始字节、JSON token、对象字段、索引、文本和 Receipt 声明都视为不可信。适配器必须在任何索引、比较、渲染或状态创建前完成限长、UTF-8、规范语法、闭合结构、语义引用与完整性检查。

## 主要风险与控制

- 宽松 JSON：使用专用字节解码器，拒绝空白、注释、尾逗号、重复键、浮点和非规范转义。
- 资源耗尽：双文件固定上限、嵌套深度 256、条件深度 16，并沿用 Runtime Pack 数组上限。
- 内容泄漏：diagnostic 只发布静态 code 和安全 JSON Pointer，不回显值、文件路径、hash 或底层错误。
- 状态污染：prepared 不返回内部 Pack；create/inspect/apply 校验并复制输入，失败不部分推进。
- 能力越界：Godot 第一方禁止网络、进程、环境变量、动态脚本、文件写入与目录扫描。
- 供应链误解：Receipt 无签名，只核对 artifact byteLength/hash 与固定 compiler/profile。

## 明确不防御

R5 不把本机恶意同用户进程、被替换的 Godot 二进制、操作系统级文件篡改或签名分发纳入信任保证。输入读取为只读；跨进程真实性留给后续签名与发布轮次。
