# R12已知限制

- R10 panorama仅作历史来源证据；R11成功预览不渲染它。若Compute splat资格、米制对齐或30 FPS门失败，本轮停止，不回退到旧天空模式。
- R11只支持固定SPZ→compressed PLY→gdgs Compute链，不支持HQ环境网格、SOG权威缓存、Raster回退、图片/视频输入、AI NPC、记忆、动态任务、存档、导出或父产品接入。
- Scene Pack不包含panorama；Environment Bundle与Assembly Report是R10私有中间产物。
- 真实供应商需要人工审批、凭据、额度和等待时间；普通verify仅证明loopback与离线缓存路径。
- 30 FPS门只约束验收机器的960×540、预热后300帧中位数；不建立跨GPU性能golden。运行时允许使用可审计的确定性LOD，但必须保留完整源身份与全量转换统计，且不能降低画面稳定、对齐或第二样例门。
- 当前验收机的full-resolution 1.92M Compute实测只有约7.0–7.5 FPS，不能作为运行资产。R11改用保留全量身份的确定性640k MPMM LOD；主资格样例与第二来源Niantic样例均以明显高于30 FPS的300帧中位数通过，连续固定帧无宏观闪动。2026-08-13用户已人工确认修正后的移动、视角、四面墙体、物体碰撞、Meshy资产、Action终端和人物落地。
- 第二来源Niantic hornedlizard证明不同来源SPZ可以经过同一解码、LOD、Bundle、Assembly与Godot Compute链；它仍复用中性Runtime和资格collider，因此不证明该物体点云与该collider在语义上匹配。
- R11.5自动门只证明一次性工程导入成功，以及headless环境严格拒绝不可用的Compute渲染；它不证明GPU画面、真实视差、1.92M splat性能或视觉/碰撞对齐，这些仍是R11.6人工硬门。
- Creator继续使用冻结R10协议和界面；空间宿主只提供R10已验证run与R11 overlay的交集，不新增浏览器API或空间参数编辑器。
- 冻结Validator在浏览器中依赖Ajv运行时代码生成；因此仅loopback Creator宿主的CSP允许同源脚本使用`unsafe-eval`。外部脚本、外部连接、frame、object和CORS仍禁止。
- Git回退不会删除仓外run、供应商任务、下载物或远程Marble world。
- R11不是初版闭环证明。新增R12必须用最初的末班地铁案例，从自然语言输入实际贯通正式人物、环境/道具资产、全部Pack/Spatial组装和Godot游戏运行时，并以另一非题材专用样例证明可泛化；完成前不得宣称自然语言到3D初版闭环。
- R12真实链路已能从Creator启动Godot并到达ending，但用户人工验收确认出生点、资产与Action终端的空间布局仍奇怪，可玩性和整体体验未达到初版门槛。因此R12只证明技术可达性，`docs/MVP_STATUS.json`继续保持`claimAllowed=false`。
- 后续轮次必须先审计当前Creator组装、空间推断和自动验证的系统性偏差，并评估采纳或复用Godogen等开源框架的方法。在形成可泛化的架构改造前，不应继续用末班地铁的案例坐标或局部参数调节代替系统修复。
