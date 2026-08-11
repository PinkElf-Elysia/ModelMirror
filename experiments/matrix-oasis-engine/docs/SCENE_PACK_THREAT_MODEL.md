# R7 Scene Pack 威胁模型

## 不可信输入

- Scene manifest、Runtime Pack、Receipt 与所有 GLB 字节均视为不可信。
- 路径、文件大小、哈希、GLB chunk、glTF JSON、索引和 node binding 均需独立验证。
- 输入不得触发网络、环境变量读取、进程执行、脚本加载或文件写入。

## 固定防线

- Manifest 最大 256 KiB，fatal UTF-8、无 BOM、严格 canonical JSON、深度不超过 256。
- 资产路径只能是 `assets/` 下 POSIX 相对 `.glb`，拒绝绝对路径、穿越、URI、symlink 与 junction。
- 读取前后复核 realpath、文件身份、长度与 SHA-256；单文件 32 MiB、总计 128 MiB。
- GLB 只接受 version 2、精确 chunk/长度、内嵌 buffer/image；拒绝 camera、light、animation、skin、外部 URI 与未知可执行扩展。
- Godot 组合使用候选树，全部验证成功后一次替换；失败保留旧世界、Runtime session、玩家与终端。
- 诊断只包含静态 code、phase 和安全 JSON Pointer，不回显输入值、绝对路径或底层异常。

## 非目标

R7 不防御拥有本机同等权限且能在最终系统调用之间持续替换文件的恶意用户。它保证可观察替换 fail closed，不把 provider provenance、Scene hash 或资产 hash描述为签名或信任证明。
