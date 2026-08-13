# R11已知限制

- R10 panorama仅作历史来源证据；R11成功预览不渲染它。若Compute splat资格、米制对齐或30 FPS门失败，本轮停止，不回退到旧天空模式。
- R11只支持固定SPZ→compressed PLY→gdgs Compute链，不支持HQ环境网格、SOG权威缓存、Raster回退、图片/视频输入、AI NPC、记忆、动态任务、存档、导出或父产品接入。
- Scene Pack不包含panorama；Environment Bundle与Assembly Report是R10私有中间产物。
- 真实供应商需要人工审批、凭据、额度和等待时间；普通verify仅证明loopback与离线缓存路径。
- full-resolution 1.92M splat的30 FPS门只约束验收机器的960×540、预热后300帧中位数；不建立跨GPU像素或性能golden。
- R11.5自动门只证明一次性工程导入成功，以及headless环境严格拒绝不可用的Compute渲染；它不证明GPU画面、真实视差、1.92M splat性能或视觉/碰撞对齐，这些仍是R11.6人工硬门。
- Creator继续使用冻结R10协议和界面；空间宿主只提供R10已验证run与R11 overlay的交集，不新增浏览器API或空间参数编辑器。
- 冻结Validator在浏览器中依赖Ajv运行时代码生成；因此仅loopback Creator宿主的CSP允许同源脚本使用`unsafe-eval`。外部脚本、外部连接、frame、object和CORS仍禁止。
- Git回退不会删除仓外run、供应商任务、下载物或远程Marble world。
