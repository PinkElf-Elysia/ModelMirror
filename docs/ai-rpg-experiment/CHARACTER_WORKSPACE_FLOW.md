# 角色创作与群聊目录联动

本轮在 R2 独立工作树中补齐版本封存、群聊选择和页面导航。只连接本地独立副本，不迁移原始材料或旧数据；不改变群聊、单角色及世界运行合同。

## 宿主集成

- `startAuthoringServer` 返回可信进程内 `works`（原 `AuthoringStore` 实例）。将它同时传给 `GroupStore.open` 与 `startGroupServer`，不再复制版本或维护手工白名单。存储仍只有一个所有者锁。关闭群聊宿主后再关闭创作宿主，不另行关闭 `works`。
- 创作启动参数 `characterSettings.groupsUrl` 与群聊启动参数 `charactersUrl` 可选，仅接受 `http://127.0.0.1:<port>/groups` 或 `/characters`。在初始化前设置；不从浏览器参数读取服务地址。
- `/rpg-app/api/authoring/characters/navigation` 返回创作到群聊的导航地址；群聊 bootstrap 返回反向地址。未配置时隐藏相应入口，原独立入口继续工作。
- 新群聊 bootstrap 实时读取可信作品存储中已封存的角色版本，包含所有可用版本。草稿和世界卡不成为角色候选。选择项始终传递 `workId/versionId`，不把名字或排序位置当作版本身份。
- 群聊页面允许来自另一页面的顶层 GET 文档导航；API 仍检查 Host、同源、所有者 Cookie 和写入 CSRF，未开放跨源 API 读取或发送。

## 用户流程

1. 角色库打开草稿，保存后点击“创建版本”。
2. 在版本预览中点击“用此版本创建群聊”，创建页预选该精确版本。选第二位角色、共享场景和开场，创建群聊。
3. 也可通过“我的群聊”进入列表。每次打开创建页重新读取目录；页面已打开时点击“刷新状态”，保留角色的精确版本、模型和场景输入。版本已不存在时保留空选择并禁止创建，不静默替换。
4. 群聊页可通过“角色库与创作”返回。未保存创作或未提交群场景会触发离开页面提示。

同一模板可选多个实例；默认优先选择不同作品的最新版本。已经创建的群聊仍保存原版本引用和配置哈希。封存新版本不会更新旧成员、旧开场或旧消息；旧群恢复仍验证原绑定。

## 离线证据

- `node --test experiments/ai-rpg-engine/character/group/*.test.mjs experiments/ai-rpg-engine/character/editor.test.mjs experiments/ai-rpg-engine/character/http.test.mjs experiments/ai-rpg-engine/character/session.test.mjs`：通过 63 项（主回归执行时）。
- 后续选择保留修正与补测：`node --test experiments/ai-rpg-engine/character/group/browser.test.mjs experiments/ai-rpg-engine/character/group/workspace.test.mjs`：通过 13 项，包含一个新增用例；与主回归有重叠。
- `node --test experiments/ai-rpg-engine/authoring/server.test.mjs experiments/ai-rpg-engine/authoring/library.test.mjs`：通过 13 项。
- 集成回归验证：草稿不可选、封存后可选、新群读取新开场、旧群记录和版本内容不变、重启恢复、跨源 API 拒绝。没有派发模型请求。
- 本地正式素材副本：桌面页面完成内容不变的新版本封存，看到携带精确版本的群聊链接；本地 API 完成新群创建及旧群完整事件记录一致性核验。
- 浏览器跨页点击时 Computer Use 因 URL 识别限制终止。跨页点击和此次移动端视觉验收标为未验证；接口测试与 DOM 交互测试不能替代人工页面验收。

运行指纹涉及的文件均未改动；没有扩展旧哈希接受范围。原验收结果（包括模型表现偏差）继续保留。本轮不主张重新完成旧存档全历史矩阵或真实模型验收。

## 后续验收补充

以上记录保留为本批次历史结果。后续独立 Chromium 已完成 1280、390、320px 共 18 项跨页与刷新工程流程，详情见 [CHARACTER_WORKSPACE_UI_ACCEPTANCE.md](CHARACTER_WORKSPACE_UI_ACCEPTANCE.md)；不改写此前 Computer Use 中断记录，也不代表真实手机或用户人工体验已验收。群创建字段现在按当前标签页暂存，角色备份恢复后显示未保存；未发送聊天输入仍不保证刷新恢复。

## 回退

停止本轮独立预览，撤回本轮导航和宿主接线差异即可。保留独立数据目录与所有旧版本，不回写原素材、旧群或原证据。没有提交、推送、部署或真实模型调用。
