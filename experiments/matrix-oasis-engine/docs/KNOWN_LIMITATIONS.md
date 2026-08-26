# R16资格完成后的已知限制

- R15已在中性与末班地铁缓存上补齐实际InputMap重放、逐节点图形、完整录像和独立300帧实时性能证据，并通过人工验收。
- R16已让Creator复现同一profile并通过双真实案例人工验收；该结论只适用于锁定的Godot 4.6.3 Windows资格profile。
- R16首次资格会执行完整本地Godot证据链，耗时和仓外媒体占用高于旧Creator；只有同一已资格Solution的后续启动可跳过重新取证。

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
- R13只提取Spatial Intent与Environment Facts，不输出最终placement、player spawn或Action terminal坐标，不切换Creator或产品预览，因此本轮不会直接改善R12成品体验。
- NavigationMesh和anchor只在锁定的Godot 4.6.3 Windows工具链上要求20次canonical字节确定；不建立跨Godot版本或跨平台浮点golden。
- wall anchor是由真实碰撞查询推导的可用表面候选，不等同于语义上的“墙面用途”。R14仍需结合Intent求解并执行最终物理复验。
- 历史R14.7人工验收前，Creator默认预览仍使用旧路径；当时新增solution和solved overlay不构成产品默认切换或初版完成声明。
- 求解器只支持固定R14 profile：4 zones、6个非环境placement、16个node context和每节点64 actions；超限直接失败，不部分发布。
- 历史R14阶段Creator不能复现同一求解/复验/overlay发布链，第二个真实环境未从Creator入口重新资格；R16已完成迁移并通过双案例验收。
- R15当时已将中性真实缓存重新资格为当前合同，并与末班地铁走同一实际输入证据链；R16随后让两案都通过Creator入口复现和MVP重新资格。
- 现有通用实现不含地铁题材ID或案例坐标，但仍采用固定视觉占用阈值、单一全局支撑高度及旧walkable envelope兼容入口；非平面、多层、不同点云噪声或碰撞拓扑可能需要新合同，而不是继续调节单一样例参数。

# R17第二版选型限制

- R17只完成来源锁、仓外资格、评分和架构边界，不把LimboAI、Dialogue Manager或Mem0加入产品依赖，也不实现AI NPC、记忆、对话或事件功能。
- R17证伪复验后没有任何外部行为树、对话或记忆候选达到`recommended`或`backup`；R18必须继续使用现有Runtime状态机、原生Control和Ledger派生索引边界。
- LimboAI的固定Godot 4.6.3 Windows trace稳定，但实际运行包的CC-BY许可证面和GDExtension二进制来源未闭合，当前包被拒绝；不得据此引入依赖。
- Dialogue Manager只在禁用状态变更、成员访问和资源内加载的Compatibility fixture中运行，且仍观察到资源泄漏；未证明Forward+资格。
- Mem0只验证了SDK对测试自建loopback API的传输；测试中的add/search/correct/delete/export语义来自夹具自己的Map，不是Mem0本地记忆实现。
- 当前Windows无容器Harness可以净化环境变量并终止超时进程树，但不能强制文件系统或网络隔离；相关硬门统一为`not-proven`。
- Letta与动画夹具均为deferred；本轮未申请容器，不以README或项目热度替代运行证据。
- R17没有把WorldX设为默认架构；其时间线和阶段化编排仅作为参考，并受现有R16权威合同约束。
