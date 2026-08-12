# R10已知限制

- Marble环境视觉是360°panorama，没有视差；collider只提供原型级碰撞。
- 不支持SPZ、HQ环境网格、图片/视频输入、AI NPC、记忆、动态任务、存档、导出或父产品接入。
- Scene Pack不包含panorama；Environment Bundle与Assembly Report是R10私有中间产物。
- 真实供应商需要人工审批、凭据、额度和等待时间；普通verify仅证明loopback与离线缓存路径。
- R10.5已接通Creator、same-origin宿主与R10 Godot wrapper；普通verify仅使用合成离线Environment/Scene输入，真实缓存完整预览与Marble资格仍需R10.6人工验收。
- 冻结Validator在浏览器中依赖Ajv运行时代码生成；因此仅loopback Creator宿主的CSP允许同源脚本使用`unsafe-eval`。外部脚本、外部连接、frame、object和CORS仍禁止。
- Git回退不会删除仓外run、供应商任务、下载物或远程Marble world。
