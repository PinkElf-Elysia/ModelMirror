# R7 已知限制

- R7 是本地 Scene Pack 与 GLB 场景绑定底座，不是完整资产管线或关卡编辑器。
- 只提供四个 Kenney CC0 静态验证资产及其固定共享纹理；figurine 上游 animation 声明会在通过原始字节验证后于内存中移除，运行时仍是静态占位。不提供 NPC、NavigationAgent3D、动画播放、音频或生产级碰撞烘焙。
- 控制器不含跳跃、冲刺、蹲伏、手柄、无障碍替代输入或可配置键位 UI。
- Runtime Pack 仍不描述 3D 坐标；Scene Pack 是独立 sidecar，Action 终端仍按 R6 确定性网格生成。
- 只接受 GLB 2.0；不支持 glTF 外部资源、SPZ、PLY、SOG、压缩包、远程 URL 或流式加载。
- Scene hash 与资产 hash 只做完整性检查，不是签名或来源认证。
- Marble/Meshy 本轮零真实调用；gdgs 资格结论不等于正式依赖或 SPZ 支持。
- 物理位置只使用容差断言；固定帧是单机人工证据，不做跨 GPU 像素级 golden。
- Godot 需要仓外用户缓存；受限沙箱若禁止系统目录写入，验证必须在正常进程权限下运行，但不得提交缓存。
- GdUnit4 仍是 dev-only vendored 依赖；官方 demo 文件仅作为不可执行 MIT 参考，不作为运行依赖。
- 不提供导出模板、桌面安装包、存档、回放、AI、网络或父项目适配器。
- 模块仍为 private/UNLICENSED；既有 esbuild low 告警和已批准 caniuse-lite CC-BY-4.0 例外不变。

移除任一限制必须进入后续批准轮次并补齐验收和回退。
