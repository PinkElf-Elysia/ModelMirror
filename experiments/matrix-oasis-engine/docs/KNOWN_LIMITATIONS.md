# R4 已知限制

- R4 只建立 Godot 工程、测试和验收底座，不消费 Runtime Pack，也不提供玩法或运行时桥接。
- Bootstrap 使用内建 primitive，不代表美术、关卡、资产导入、物理或导航能力。
- Forward+ 图形证据是单机人工验收，不做跨 GPU 像素级 golden 比较。
- Godot 需要创建自身的仓外用户缓存；受限沙箱若禁止该系统目录写入，验证必须在正常进程权限下运行，但不得改写系统路径或把缓存提交进仓库。
- GdUnit4 是 dev-only vendored 依赖；R4 不承诺其他 Godot patch/minor 版本兼容。
- GdUnit4 上游测试资源含 Windows 深路径；R4 拆分 harness 仅对其创建的 Git 子进程启用 long-path 支持，其他外部 Git 客户端仍需自身支持长路径。
- MCP 资格验证不等于正式接入；结果只供后续轮次选择。
- 不提供导出模板、桌面安装包、存档、回放、AI、NPC、Marble、3D 资产或父项目适配器。
- 模块仍为 private/UNLICENSED；既有 esbuild low 告警和已批准 caniuse-lite CC-BY-4.0 例外不变。

移除任一限制必须进入后续批准轮次并补齐验收和回退。
