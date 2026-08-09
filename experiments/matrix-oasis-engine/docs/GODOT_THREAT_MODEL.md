# Godot R4 威胁模型

## 保护目标

- 不执行或写入父仓、模块外路径、共享栈或任意凭据。
- 不把引擎二进制、生成物、测试报告或图形证据提交进仓库。
- 第三方 addon 必须可复现且未经修改。

## 控制

- exact Godot 版本 doctor 与仓外 SHA-512 记录。
- 正向路径 allowlist、第一方 GDScript 能力扫描、二进制/生成物拒绝。
- GdUnit4 upstream/tag/commit/license/archive/tree digest 锁定。
- 自动 headless 与人工图形门分离，避免 GPU 环境污染基础验证。
- MCP 只在一次性副本、loopback、无凭据环境运行，并比较验证前后文件树。

R4 不宣称抵御拥有同用户文件系统权限的恶意本地进程；此类进程可在验证后篡改仓外工具或证据。
