# 可选插件候选：运行、合同与回退

本候选在独立分支 codex/rpg-plugin-branch-save，基于 bac37a6e。目录只含审核的第一方插件 rpg.branch-save@1.0.0；不支持下载或执行外部包。可信代码错误可隔离，不承诺第三方代码安全沙箱。Studio原布局和旧RPG合同不变。

## 生命周期合同

- `GET /rpg-app/api/plugins` 返回目录、精确版本、artifact/manifest SHA256及登记revision。POST同前缀 `/:id/install|uninstall` 校验操作ID、版本/hash及预期登记revision。
- 会话 `GET /rpg-app/earth/api/sessions/:id/plugins/rpg.branch-save` 查看启用状态；POST其 `/enable|disable` 另校验sessionId、预期会话revision及启用时的完整权限清单。安装不会启用，新分支不复制授权；卸载撤销全部会话授权。升级不自动安装。
- POST会话 `/branches` 接收 `{operationId,expectedSessionRevision,turn,name}`。会话必须版本匹配、已完成完整回合且无在途/未确认请求。相同操作和内容重放返回同一会话；改变内容拒绝。GET插件 `/nodes` 读取后台节点，要求仍授权。
- 权限仅本会话完成历史读取、受控分支准备与界面贡献。插件不能直接修改提示词、文件、联网或派发模型。节点保存和核心分支发布由宿主完成；提交时再次校验授权epoch。所有写操作同源校验，前端不能提交system或供应商配置。
- 同一数据目录只支持单宿主实例。owner.lock冲突应先核对进程，不可无条件删除。故障时关闭插件能力、保留核心历史；读写失败不得当成空白数据重新初始化。

## 数据与版本

`RPG_DATA_DIR/earth/`保留普通会话及`branches/`原子封套；封套中的核心session、不可变snapshot、插件节点和幂等操作分字段保存。`plugins/registry.json`登记安装/授权，`dispatches/earth/`为所有路线共用的原卡账本。节点名不注入模型。复制会话不会复制可执行请求、授权或预算。

新会话由服务器绑定作者文本、装配器、卡片、模型配置及存储源码hash，另绑定角色/世界/参数。匹配时才可续玩；旧会话无充分证据保持可读，不追认或迁移。完整原始消息和Provider原文保留；渲染限制不改变原文。当前账本采用原服务目录计数，禁止通过新目录或调高配置绕过原授权。

## 候选构建与无网检查

从 `experiments/ai-rpg-engine/` 运行：

```powershell
docker build -f studio/Dockerfile -t modelmirror-rpg:plugin-b4 .
docker run --rm --network none --read-only --tmpfs /data:rw,uid=1000,gid=1000,noexec,nosuid --entrypoint node modelmirror-rpg:plugin-b4 studio/plugin-container-smoke.mjs /data/check <当前runtimeHash>
```

runtimeHash从DELIVERY.json取得。检查只用新建tmpfs数据，强制Provider关闭和预算0；验证两卡静态资源、安装不启用、离线完整回合分支、幂等、卸载/宿主重启/读取与续玩、快照及父路线不变、重装不恢复权限。不是两卡真实模型或内容质量验收。不要把这些命令改成挂载已有用户卷的烟测。

Docker构建上下文由studio/Dockerfile.dockerignore排除.env、原件私有目录、会话、账本、node_modules及宿主构建产物。Dockerfile仅将锁定依赖、明确模块代码和作者资源复制到镜像；供应商密钥不在镜像、前端或提示词中。父前端另运行client的npm.cmd run build，RPG路由沿既有代理，不修改共享Compose。

## 保留配置与恢复

本批没有共享部署授权。后续发布须独立确认镜像、父前端、现有数据卷、原预算及服务配置的绑定，不重建空账本，不暴露凭据。先只读核对现有会话/分支/回执清单并保留备份；只在明确授权后用原数据目录与相同额度运行，禁止自动派发或重放pending。

优先停用插件回退，保留当前核心服务读取新分支格式。卸载不删除会话、节点或账本。旧B1及更早宿主无法枚举新branches目录，因此尚不允许直接降级镜像；需要先在数据副本验证读取能力。不要修改旧会话runtime来伪造兼容。恢复未确认生成先读原记录，不换ID重发；分支使用同一操作ID核对已提交结果。

只停止已核对归属的本批预览/临时容器。保留18433预览、历史B1/B2/B3清单和全部证据；本文不是操作共享8000/5173的授权。


## M1 历史窗口 B1 候选（2026-09-19）

新增审核插件 `rpg.history-window@1.0.0`，仅允许 M1 format=3 会话授权；默认未安装/未启用。配置默认最近3个完整回合、原始开局资料补入关闭。完整历史始终保留，不自动总结或更新人物状态。
本批仅实现策略、插件生命周期、新运行绑定、配置原子存储与请求准备，尚未接入 `studio/server.mjs`；M1 的实际发送/分支显式拒绝直到 B2。现有入口继续使用旧宿主，不把候选文件发布到共享栈。
配置保存失败可能留下 pending 操作：使用原 operationId 查询收束；确认未应用时不会自动重新应用。成功但丢失响应的操作直接返回原完成记录。禁止更换操作 ID 绕过未知状态。
配置和授权共同决定 historyPolicyRevision；停用恢复完整历史，未知权限状态拒绝继续，不自动改变窗口或模型。停用/卸载不删除配置、历史或账本。
当前证据与回退见 `docs/ai-rpg-experiment/history-window/B1-REVIEW.md`，当前源码登记见同目录 `B1-DELIVERY.json`。旧登记保留，不将 B1 的离线结果当作 B2 实际派发或 B4 真实模型验收。


## M1 历史窗口 B2 候选

B1段落为历史批次。本批通过服务端 `historyWindow:true` / `RPG_HISTORY_WINDOW_ENABLED=true` 显式选择 M1 format=3 宿主，默认关闭；只用于新隔离数据目录，正式UI接入留B3。保持 `RPG_ENABLED=false` 与原预算边界，启用M1本身不构成真实调用授权。
设置接口为 `/rpg-app/earth/api/sessions/:id/history-window`。GET返回当前配置、授权和策略token，可携带唯一 `operationId` 只读查询；POST需同源，严格携带 `operationId/expectedSessionRevision/expectedConfigRevision/turns/includeInitialCharacter`。未知结果用相同ID和相同payload收束，返回 `not-applied` 不自动重新保存；不能更换ID绕过pending。
M1发送必须携带 `expectedHistoryPolicyRevision`，并沿用请求ID、会话及模型revision；宿主决定选取原文，前端不能提交history/system。停用后完整历史恢复，未知授权拒绝继续。分支继承分支点设置但不继承授权；模型切换、分支和插件重装均不增加预算。
本批仅本地假Provider HTTP验证，报告和当前登记见 `docs/ai-rpg-experiment/history-window/B2-REVIEW.md`、`B2-DELIVERY.json`。旧B0/B1原型、会话和证据不变；停用是首选回退，保留全历史/配置/账本，旧宿主降级另用副本验证。


## M1 历史窗口 B3 候选

B1/B2段落为历史批次。本批市场与会话设置已接入，帮助见 /help/set-rpg-history-window。正式入口仍使用服务端显式 M1 开关，默认关闭，不迁移旧会话。
构建地球卡用于Studio时必须执行 npm.cmd run build --prefix experiments/ai-rpg-engine/card-replica -- --base=/rpg-app/earth/；父前端另在client运行 npm.cmd run build。只运行默认card build会得到不适用于嵌套入口的 /assets 路径。
本批18455/18456为独立离线验收实例，数据在 .rpg04-work/history-window-b3/preview-data，Provider关闭、预算0。18453保留为已批准原型。本文不授权共享栈更新或真实派发。
设置未知结果使用界面“恢复设置结果”，查询或收束原操作；不清浏览器数据、不换ID重放。停用/卸载保留配置和完整历史；重装需重新启用。旧宿主降级另用副本验证，不能直接复用含新插件登记的数据目录。
当前证据与hash见 docs/ai-rpg-experiment/history-window/B3-REVIEW.md、B3-DELIVERY.json。B3用户验收及B4真实额度仍待后续确认。


## M1 已验收候选收尾（2026-09-19，本机时区）

上述B1–B3为历史记录。B0–B3与B4三份真实输出均已获得用户确认。新4次额度计入1次必要认证及3次Gemini生成，已耗尽；不追加、不重试，不沿用旧额度。真实入口18461、宿主18459、独立控制面18458保留用于只读审阅；18455离线入口及18453原型保留。
真实会话当前启用窗口1、开局资料关闭；第三轮实际请求只包含第2回合，完整三轮历史仍保存。重要：全局 /rpg-app/api/status 的 contextPolicy:retain-all 来自静态卡片默认描述，不能代表逐会话有效策略。运维核对应读取 sessions/:id/history-window 的 effectivePolicy 和回合 historyPolicy，再比对派发 request.json；本次未为纠正文案而改变已验收运行hash。此字段的语义澄清列为后续发布前复核项。
收尾说明与当前hash见 docs/ai-rpg-experiment/history-window/FINAL-REVIEW.md、FINAL-DELIVERY.json。没有Commit/Push/PR/共享部署授权。回退优先停用插件，不删除会话、分支、配置或调用账本；旧宿主降级必须在数据副本上验证。
