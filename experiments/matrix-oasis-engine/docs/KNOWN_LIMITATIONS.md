# R10已知限制

- Marble环境视觉是固定中心的360°panorama，没有平移视差，也不承载房间几何。玩家平移时背景只随视角旋转，独立collider与Meshy物体会移动，但背景缺少对应空间线索；真实室内场景人工验收确认该组合存在明显的系统性体验偏差。R10只把它认定为技术闭环和原型级碰撞通过，不宣称沉浸式室内环境体验通过。
- 不支持SPZ、HQ环境网格、图片/视频输入、AI NPC、记忆、动态任务、存档、导出或父产品接入。
- Scene Pack不包含panorama；Environment Bundle与Assembly Report是R10私有中间产物。
- 真实供应商需要人工审批、凭据、额度和等待时间；普通verify仅证明loopback与离线缓存路径。
- R10.6真实缓存已确认panorama加载、Marble碰撞、第一人称移动及Meshy prop/静态人物显示；全景背景与可移动世界之间的空间一致性仍须后续轮次先审计方案再修复，不能以调整样例或遮掩背景替代架构决策。
- 冻结Validator在浏览器中依赖Ajv运行时代码生成；因此仅loopback Creator宿主的CSP允许同源脚本使用`unsafe-eval`。外部脚本、外部连接、frame、object和CORS仍禁止。
- Git回退不会删除仓外run、供应商任务、下载物或远程Marble world。
