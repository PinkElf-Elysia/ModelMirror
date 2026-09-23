# M3 B4 UI、HTTP 与封装验收候选

状态：**等待用户 B4 确认**。B0–B3 已确认。B5 真实 Provider 未运行，授权 0、已用 0。本报告的离线内容不是提取忠实性或剧情质量证据。

## 基线与范围

工作区 `C:/tmp/modelmirror-rpg-memory-palace`，分支 `codex/rpg-memory-palace`，基线 `86fdb953590e625dffeb073137357cddb3af267f`。保护脏主工作区、旧会话、历史证据和既有入口文件。未 Commit、Push、PR、共享部署或调用真实模型。原作者/M2 的 7 份冻结源码及 B0 的 4 份交付文件 hash 均未改变。

本批分小组完成 HTTP 宿主、会话浮层、市场帮助、UI 测试和交付登记。实际生命周期、条目、召回、原子存储与受控调用仍在独立 RPG 模块，父仓只增加市场内容、帮助与测试，未改 Studio 布局。

## 可见验收入口

- 聊天：http://127.0.0.1:18491/rpg/earth
- 市场：http://127.0.0.1:18491/rpg/plugins
- 帮助：http://127.0.0.1:18491/help/use-rpg-memory-palace
- 点击“历史”，打开虚构角色“林遥”已有 1 回合的离线会话，再选“插件 → 打开记忆宫殿”。模型明确显示“离线样例（不调用模型）”。未发送草稿保留。

人工可检查搜索、编辑、关键词与/或、用户/AI 角色、取消/Esc、保护、修订、删除恢复、停用和重装。保存、启用、选模型不会生成内容。当前入口仅用于离线 B4 验收。

## 实际验证

| 检查 | 命令 / 证据 | 结果 |
|---|---|---|
| 插件与宿主回归 | `node --test experiments/ai-rpg-engine/studio/*.test.mjs experiments/ai-rpg-engine/plugins/*.test.mjs` | 250 通过，1 既有跳过，0 失败 |
| 新 HTTP 流程 | `node --test experiments/ai-rpg-engine/studio/memory-http.test.mjs` | 4 通过；计入上述全套 |
| 地球卡 | 卡片目录 `npm.cmd run verify` | 26 测试通过，TypeScript / Vite 通过 |
| 实际嵌入构建 | 卡片目录 `npm.cmd run build -- --base=/rpg-app/earth/` | 通过，最终服务此产物 |
| 受影响前端 | client 下运行 RpgMemoryPalace、RpgRollingSummary、RpgPluginsPage、RpgModelSelector、RpgHistoryWindow、RpgChatState、HelpCenterPage、StudioBetaPanels | 8 文件、99 测试通过 |
| Studio 生产构建 | client 下 `npm.cmd run build` | 通过，既有大 chunk 警告保留 |
| 帮助图片全局校验 | client 下 `npm.cmd run verify:help-images` | 3 项基线失败，见下文；新增 2 图通过 |
| 代码与冻结 | `git diff --check`、冻结 hash 比对 | 通过；无依赖升级 |

跳过项是依赖私有 B5 会话副本的原有 `model-legacy-copy.test.mjs`，不冒充此次旧数据实测。日志保存在 `experiments/ai-rpg-engine/.rpg04-work/memory-palace-b4/`：`host-final.log`、`http-final.log`、`frontend-final.log`、`card-final.log`、`card-production-base.log`、`parent-build-final.log`、`help-images.log`。

全局截图校验失败文件均已用 `git show HEAD:<path>` 与现场逐字对比，确认未改动；基线帮助正文也未引用它们：

- `client/public/help-center/b266b870/studio-beta-headings.jpg`
- `client/public/help-center/eeb5bbd2/creator-cache.jpg`
- `client/public/help-center/eeb5bbd2/studio-layout.jpg`

本轮不清理其他任务资产，不把该全局检查写成通过。

## HTTP 和浏览器证据

实际 HTTP 测试覆盖安装/授权分离、严格字段与同源拒绝、过期 revision、原操作恢复、人工保护、完整剧情落盘后状态、旧会话只读，以及 M2 后台＋M3 连续三轮协调与停用。B3 已保存的 20 组实际消息捕获证据继续保留，本批不改写为真实 Provider 证据。

正式内置浏览器完成：市场安装 → 会话授权 → 显式选离线模型 → 新增受保护条目 → 编辑取消/Esc → 离线发送一轮 → 后台合法无变更更新 → 查看整理原文与召回 → 改为全部命中 → 解除保护/删除/恢复 → 草稿刷新 → 停用只读 → 卸载/重装 → 确认授权仍关闭 → 明确重新启用。

桌面 1280×720 和 390×844 视口实际检查；手机编辑内容通过纵向滚动访问，Tab/Esc 路径核对。未宣称物理手机或跨浏览器完整验收。截图目录 `.../memory-palace-b4/screenshots/` 包含真实 `desktop-palace.jpg`、`mobile-palace.jpg`、`mobile-editor.jpg`、`market.jpg`。帮助用 PNG 仅由原图等比转换/调色板压缩，手机 390px 等比放大至780px；未生成或补画界面。

## 重启恢复发现

桌面重启终止了本轮旧预览，插件 owner.lock 保留 PID53552，导致新预览的插件服务拒绝启动（PLUGIN_STORE_ALREADY_OWNED），普通 HTTP200 不能证明可用。核实旧 PID 不存在、端口与新 PID37384 属于本轮后，仅停止新预览，将旧锁归档为 `owner-53552-abandoned.json`，启动本轮 PID14084。注册、会话、条目、模型选择和派发记录保留；浏览器点击“恢复记录”后状态恢复，无自动重放。

这是受控人工恢复记录，不宣称已有自动清理遗留锁功能。禁止照抄历史 PID 停进程，禁止在未确认原所有者退出时移走锁。

## 操作与回退

已构建后，从本工作区运行 `node docs/ai-rpg-experiment/memory-palace/preview-app.mjs` 可启动独立离线预览，前端18491、宿主18492，数据仅在 `experiments/ai-rpg-engine/.rpg04-work/memory-palace-b4/preview`。已有入口运行时无需再启动。实时先核对端口和进程归属。`preview/preview.json` 只记录本轮 PID/端口/模式；服务 Provider 关闭、真实预算0。离线任务账本与真实账本分离。不得把离线额度转成真实授权。

生产宿主的 `RPG_MEMORY_PALACE_ENABLED` 默认关闭；需显式开启才创建 M3 新版本会话，本轮不更改共享栈。服务内部复用已批准控制面契约，前端不能提交供应商地址、凭据、system 或历史切片。

首选回退：会话停用 M3，恢复原有 M1/M2 组合，保留条目、修订、历史、分支、账本。卸载撤销授权但保留数据。旧宿主降级只能先用副本验证，不能直接读取/覆盖此新登记目录。仅可在核实归属后停止本轮自有预览，保留人工入口至验收结束。

## 待办与边界

B4 等待用户确认。B5 至少8次真实生成建议仍待另行授权和冻结（4剧情＋4记忆）；M2/M3 联合真实调用另行申请。离线已证明消息装配、后台顺序、幂等与容量选择；未证明真实提取忠实性、复杂冲突处理或长程剧情质量。所有历史失败、旧 B0 浏览器阻断和本轮恢复记录留在 STATUS，不重写为从未发生。
