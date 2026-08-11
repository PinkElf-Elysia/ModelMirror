# Kenney Prototype Kit source record

R7 从 Kenney Prototype Kit 1.0 选择四个 GLB，作为本地 visual/collider、道具和静态角色占位验证夹具。四个原始 GLB 均引用同一份 `Textures/colormap.png`，经用户人工批准后，一并保留该 CC0 共享纹理；除此之外不允许其他外部 GLB 依赖。

固定 `figurine.glb` 含 27 条上游动画声明。经用户人工批准，该精确哈希资产允许进入校验，但运行时在内存中删除 `animations` 后才交给 `GLTFDocument`，并断言组合结果不含 `AnimationPlayer`；其他 GLB 仍禁止动画。

- 上游：`https://www.kenney.nl/assets/prototype-kit`
- 官方包：`kenney_prototype-kit-1.0.zip`
- GLB 路径：`Models/GLB format/{floor-square,wall,crate,figurine}.glb`
- 共享纹理路径：`Models/GLB format/Textures/colormap.png`
- 修改：无
- 运行时供应商依赖：无

这些文件只验证离线 Scene Pack/GLB 管线，不设定最终视觉风格，也不代表 Marble、Meshy 或其他在线服务的产物。
