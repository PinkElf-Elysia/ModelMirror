# R6 已知限制

- R6 是第一人称技术骨架，不是完整游戏；只有 primitive 测试舱、动态 Action 终端与最小 HUD。
- 不提供 NPC、NavigationAgent3D、Marble、Meshy/Kenney 资产、角色模型、动画、音频或正式场景绑定。
- 控制器不含跳跃、冲刺、蹲伏、手柄、无障碍替代输入或可配置键位 UI。
- Runtime Pack 不描述 3D 坐标；终端布局是 R6 表现层的确定性投影，不是正式内容合同。
- 物理位置只使用容差断言；固定帧是单机人工证据，不做跨 GPU 像素级 golden。
- Godot 需要仓外用户缓存；受限沙箱若禁止系统目录写入，验证必须在正常进程权限下运行，但不得提交缓存。
- GdUnit4 仍是 dev-only vendored 依赖；官方 demo 文件仅作为不可执行 MIT 参考，不作为运行依赖。
- 不提供导出模板、桌面安装包、存档、回放、AI、网络或父项目适配器。
- 模块仍为 private/UNLICENSED；既有 esbuild low 告警和已批准 caniuse-lite CC-BY-4.0 例外不变。

移除任一限制必须进入后续批准轮次并补齐验收和回退。
