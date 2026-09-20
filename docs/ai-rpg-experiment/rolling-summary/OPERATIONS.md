# M2 操作与回退

独立本地候选，基线 `87431327f3ef27843caa7ebe3115454e06d8ffbe`，分支 `codex/rpg-rolling-summary`。B0–B4 已确认；B5 实际五份输出均获人工通过。累计 3 次剧情＋2 次摘要（首次＋一次增量），额度 5/5，剩余 0。更长滚动、真实后台时机及第二版摘要后的剧情尚未运行。未 Commit、Push、PR 或共享部署。

## 入口与复核

工作区根 `C:/tmp/modelmirror-rpg-rolling-summary`。保留两个独立入口：

- 真实审阅：<http://127.0.0.1:18473/rpg/earth>，后台 18471。历史 → 林遥（三回合）→ 插件 → 自动总结 → 设置，可读第二版摘要、原文与修订，覆盖至第 2 回合。剩余 0 次，发送禁用；刷新/切换不会派发。
- 离线验证：<http://127.0.0.1:18469/rpg/earth>，后台 18467。`rolling-summary-b4/preview-data-v3` 为虚构样例。真实额度 0，仅注入离线 Provider；不能作为内容质量证据。

真实数据在 `experiments/ai-rpg-engine/.rpg04-work/rolling-summary-b5/data`；slot-1…5 分别保存 request、response 原文/SSE、回执与派发记录。原 4 次授权和新增仅摘要 1 次授权分开保留。控制面 18458 复用既有有效资格，认证 0 次，连接和凭据仍由模镜控制面管理。运行实例内存传递配置，不打印、写文件或写入前端凭据。

先按 owner 文件、监听端口和完整进程命令重新确认本轮进程归属；PID 不是长期身份。不得重复启动或例行关闭审阅入口。共享 8000/5173、M1、旧卡片保持原样。

## 构建与验证

```powershell
# card-replica 目录（Studio iframe 必须用子路径）
npm.cmd run build -- --base=/rpg-app/earth/
# client 目录
npm.cmd run build
# 工作区根：本地保存的完整测试命令及结果
Get-Content experiments/ai-rpg-engine/.rpg04-work/rolling-summary-closeout/regression.json
Get-Content experiments/ai-rpg-engine/.rpg04-work/rolling-summary-closeout/frontend.json
```

本次宿主/插件/卡片测试 183 通过、1 个既有私有样例跳过；前端 53 通过；两处生产构建通过。Studio 保留既有大 chunk 警告。B4 的早期 v1/v2 故障与 UI 证据保留，v3 的版本边界见 B4-ACCEPTANCE.md。

独立离线预览脚本 `tooling/rolling-summary-b4-preview.mjs` 只使用本地样例。B5 的 prepare/send/update 脚本是一次性冻结试验，不是日常重放入口；额度耗尽后不得再次派发。以后测试须新授权且另行冻结目标，不得清空账本或换 ID 绕过。

## 会话与接口

安装不等于启用；新会话默认关闭。首次选择摘要模型，保存设置、启用、刷新均不调用。发送前模式补齐覆盖后才生成；后台模式在完整回复落盘后总结，页面关闭不会取消已派发任务，费用按派发计。摘要失败暂停剧情，保留草稿和上一有效版本，不自动回退或重试。

M2 生效时 M1 保留设置并显示被接管；停用后恢复原 M1，否则完整历史。卸载保留数据并撤销授权，重装须重新启用。人工修改生成修订而不覆盖模型原文；取消或 Esc 放弃未保存编辑。旧会话不迁移。

`GET /rpg-app/earth/api/sessions/:id/rolling-summary` 返回实际模式、配置、覆盖和任务状态。全局 `/api/status` 的 contextPolicy 仅描述默认完整历史，不代表本会话。当前真实会话为摘要覆盖 1–2、保留第 3 回合原文，ready=true；这是下次请求准备状态，不代表第四回合已实测。

同路径 `/catalog` 为摘要模型列表；`POST /settings` 保存设置与可选修订，`/edit` 单独修订，`/update` 明确更新，`/recover` 核对原操作。写入均校验同源、字段、权限与 revision；M2 发送绑定统一上下文 revision。前端不能指定任意 system、历史切片或 Provider 地址。

生成期间设置锁定，界面区分“正在生成剧情”“正在更新摘要”；暂时读失败恢复后清除旧状态告警，真正未知写入仍提示恢复。未知结果只使用原操作 ID 核对，重启不自动重放。

## 重启与回退

B5 扩额曾遇旧 owner.lock 造成 HTTP503：仅准备失败、没有派发。恢复时先确认旧 PID 已退出、失败启动属于本轮且无在途操作，将遗留锁及启动证据归档后重启自有入口，保留 registry、会话与账本；具体记录见 B5-ACCEPTANCE.md。禁止面对未知活跃进程直接删锁；无法证明归属时停止恢复。不要把临时状态失败当作新调用机会。

优先逐会话停用 M2，保留完整历史、摘要版本、分支和派发记录。仅回退本次显示修正时，使用 `.rpg04-work/rolling-summary-closeout/before-ui` 中 hash 核对过的三个源文件及 `card-dist`、`client-dist` 构建副本，不动运行数据。当前显示修正未改运行版本，可继续读取 B5 会话。

整体旧宿主降级须使用数据副本另行验证，不能直接复用含 format 4 和新插件登记的原目录。本轮未做此降级验证，也未部署共享栈。停止实例前再次核对归属，保留全部人工入口、原文、账本和历史失败证据。
