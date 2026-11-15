# R3a 会话管理与玩家档案接口交接

基线：`2a7dfea1e8475ba2654ee4055e1c4cb544028483`（R2 合并）；实现分支 `codex/rpg-character-r3a`。本轮尚未提交、部署；此文说明候选实现，不能作为共享服务已开放的证据。

## 数据与权限

- `character/daily/store.mjs` 使用 SessionRecords 的独占所有权、CAS、原子提交和 reconcile，独立 scope `character-daily-v1`。profiles、assets、metadata、operations 共用一条本地 workspace 记录。操作采用 operationId、expectedRevision；相同输入幂等，碰撞和过期修订拒绝。写入结果不确定时需恢复，不能继续盲写。
- 玩家档案有内部 profile ID 和递增修订；名称不是登录身份或权限。快照 `modelmirror.rpg.player-snapshot/1` 固定 name、description、avatar 及 profileId/profileRevision。临时身份使用空引用。所有档案历史保留，R3a 没有删除或会话中途换身份。
- 浏览器仅提交 `{profileId,revision}` 或临时 `{name,description,avatar}`；宿主解析和验证。不能提交完整可信快照。旧 `{id,name}` 继续有效。新快照会话采用版本 2，旧版本 1 不反向容纳新字段。
- 单角色与群聊只把公开 name/description 纳入模型上下文；头像、档案引用不进入模型内容。人物设定不能冒充已发生行动或工具授权。群聊仍使用 R1 参与区间和过滤历史。
- 头像由浏览器解码为静态 PNG，最大原文件 5 MiB，长边缩至 512；服务端只接受校验后的静态 PNG（2 MiB、1024×1024 上限、CRC、块白名单、有界解压），按内容摘要保存。没有远程下载、SVG 或动画执行。

## 宿主与 HTTP

`DailyWorkspace` 是可信宿主协调器，连接已验证的单角色 host、GroupStore、AuthoringStore 和两个 loopback 地址。共享档案需把同一 workspace 实例显式传入两个宿主；不搜索其他目录，也不自动迁移共享数据。独立旧群聊宿主未启用 workspace 时保持旧身份合同，不显示不能保存的新字段。

| 接口 | 行为 |
| --- | --- |
| GET/POST `/rpg-app/api/authoring/characters/daily/profiles` | 最新档案列表 / 封存档案修订 |
| POST `.../daily/avatars`；GET `.../daily/avatars/:digest` | 保存经过验证的头像 / 本地显示 |
| GET `.../daily/sessions?q=&kind=all&archived=false` | 已验证的会话目录与玩家可见正文检索 |
| GET `.../daily/sessions/:kind/:id` | 玩家可见记录，非角色运行上下文 |
| POST `.../daily/sessions/:kind/:id/manage` | 名称和可恢复归档；pending/unknown 阻止归档 |
| GET `.../daily/sessions/:kind/:id/export?format=txt\|md` | 完成的可见历史，保留原始文本 |
| 群聊 `/api/daily/*` | 由相同 workspace 提供管理接口，沿用 Cookie、CSRF 和同源要求 |

单角色保留 Host/Origin 与 X-Authoring-Request 检查；群聊保留 HttpOnly Cookie、CSRF、同源、字段与请求大小校验。各接口只能用于本地所有者；成员 ID 不是授权凭据。

## 原子性与恢复边界

会话运行记录仍归原宿主，目录元数据归 daily。两者不是跨记录事务：先完成宿主写入，再记录本地观察到的活动时间。目录写入失败不得回滚、重放已派发请求；按原请求 ID 恢复。管理/新发送通过共享 workspace 队列协调，pending 核验后才能归档。

标题修订与宿主修订分开；搜索和读取不更新活动时间。未知旧活动时间显示“时间未知”，不以发现时间伪造。归档不删除会话，也不释放模型预留。检索只返回已验证的玩家可见消息，不读取私有记忆或未完成回执。搜索定位先捕获消息标识，再进入旧宿主，避免 URL 改写丢失命中位置；初始 SSE 快照保留该键盘焦点。

显示层使用仓库已有 marked/parse5；HTML 先转义，再按标签和属性白名单输出，禁止自动加载远程图片。原文可展开/复制；TXT/MD 导出保留原始正文，导出文件不是已经净化的 HTML，不应在其他系统直接当作可信 HTML 执行。角色卡导出没有读取 daily 或会话数据。

群聊输入草稿保存在本标签页 sessionStorage，按群 ID 隔离；刷新不派发，接话不消耗未发送输入。私密/无存储环境会提示先复制草稿。

## 精确兼容与未来边界

`daily/compatibility.json` 记录 2a7dfea1 的精确单角色/群聊描述符和本轮批准源码摘要。所有未变依赖仍须匹配旧值；新增或修改依赖须匹配批准值；未知描述符、源码漂移继续拒绝。旧存档不重写 runtimeHash、绑定、请求或回执。清单文件不做自身递归摘要比较，但仍进入当前运行描述符和宿主启动后的源码校验。

R1 conversation、世界 authoring/runtime/plugin 源码与原精确清单均未修改。测试产生的每次候选预览也有精确指纹：收尾空白修正后保留旧 preview，只在新的 preview-final 重新创建中性示例，不放宽兼容来接纳开发中间态。

R3b 后续须先定义编辑/候选/分支不可变来源、预算与回执关联、记忆/总结失效和新模板采用规则，再实现界面。本轮没有这些能力。R3c 的收藏、批量管理和重复导入识别仍待开发。R4 长对话、R5 角色书兼容、R6 CHARX/资源包、R7 表现层、R8 有界发言规则继续按原路线另行计划；不因 Agent 工作树进展自动加入多 Agent、世界或 Harness 接入。

原路线图与功能台账线索在历史角色工作树的 CHARACTER_ECOSYSTEM_ROADMAP.md / CHARACTER_FEATURE_DECISIONS.md，未视作已合并文件。本轮只落实 R3a，不复制其未提交实现或改变其他功能决断。GPL/AGPL 项目仅参考公开规范和行为，不复用实现；未新增依赖。

## 离线启动与回退

```powershell
node experiments/ai-rpg-engine/character/daily/launch.mjs C:/tmp/mm-cr3a-data/preview-final 43150 43151
```

启动器固定 fixture 模式，不读取真实网关凭据，不提供真实调用开关。目录第一次启动会创建两个原创中性角色。已在运行时不要重复启动；异常退出需核验进程所有权后使用 `--recover-dead-owner`。

回退仅停止本轮预览并撤销本轮入口；保留独立数据。旧程序不能打开新增 v2 身份记录，不得通过删除字段回写降级；旧 v1 及其数据目录保持原样。部署、迁移、真实调用及发布需要另行授权。
