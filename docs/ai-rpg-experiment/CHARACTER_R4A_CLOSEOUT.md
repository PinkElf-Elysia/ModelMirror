# R4a 提交前收口报告

日期：2026-11-14（Phoenix）。用户已接受真实结果；本轮只做代码范围整理、文档、独立副本兼容与离线回归，没有追加模型调用、提示词补丁、认证、额度、提交、推送或部署。Draft PR 仅准备本地标题和正文，未创建远端 PR。R4b/R4c 未启动。

## 基线与隔离

- 原实现：`codex/rpg-character-r4a` / `8ed5ed55f5d480968f7a2060b0036594690cafa4`，`C:/tmp/mm-cr4a` 保持原样。
- 待提交候选：`codex/rpg-character-r4a-pr` / `aebda0a16fa274dac07d44459849c8614cf3899e`，`C:/tmp/mm-cr4a-pr`。收口末尾只读核对远端 main 仍为该提交。
- 主线增量只有 Agent R9 的 36 个独立文件，与角色、authoring、帮助及依赖文件无重叠；未接入 Agent/世界/Harness 能力。
- 原 32 文件白名单复制后逐文件核验。候选仅更新收口文档、帮助验证基线和两张新中性截图；23 个 long-context 实现及测试文件与原工作树哈希一致，没有改运行指纹。
- 旧角色与 authoring 的 298 文件未变。原 32 文件、最新真实验收 114 文件、此前 780 条证据清单记录重新校验均无差异。原证据、卡片和会话没有回写。

## 功能及接口范围

独立 `character/long-context/` 提供容量计划、成员可见性过滤、整回合/消息历史窗口、开场策略、参数能力核验、配置快照、上下文预览、候选生成与恢复。旧会话通过新续聊副本启用，不迁移或放宽旧合同。

旧预算策略仅校验旧账本；新授权单独登记。原单聊、群聊、R3b 与 R4a 共用同一预算 guard，来源限额、未知请求预留与独占所有权保持。新分支不能复制额度。接口细节见 [交接说明](CHARACTER_R4A_HANDOFF.md)，真实结果见 [已接受摘要](CHARACTER_R4A_REAL_ACCEPTANCE.md)。

## 最新组合回归

原始日志与测试数据均在 `C:/tmp/mm-cr4a-pr-data`，不入库。下表不是 CI 全绿声明。

| 检查 | 本轮结果 | 本地证据 |
|---|---|---|
| Windows 角色全矩阵（含 R4a 34 项） | 218 项：215 通过、3 项旧世界失败；R4a 全通过 | character-ready.log |
| 禁网 Linux R4a | 34/34；只读源码挂载、已有镜像、network none | linux-r4a.log |
| 最新主线生成的单聊/群聊及三类世界旧存档 | 4/4 恢复、续聊、原件不可变 | latest-ready.log |
| 受控供应商与模型控制 | 58/58 | shared-short.log |
| 受控网关相关 Python | 首轮 186/187；缺失依赖的唯一失败项补齐后 1/1。合计覆盖 187，不宣称整套一次全绿 | gateway.log、gateway-ready.log |
| 父前端相关测试 | 53/56；3 项既有帮助失败 | parent-tests.log |
| 聊天前端 | 94/94 | chat-tests.log |
| 父前端构建 | 通过；保留既有 chunk size 警告 | parent-build.log |
| 聊天前端构建 | 通过 | chat-build.log |
| 中性帮助桌面/窄屏回放 | 两种宽度通过，包含参数、默认、预览、候选采用、完整刷新、输入保留；无横溢出/pageerror | help-ready/result.json |
| 帮助图检查 | 本轮两图通过；全仓仍有 24 条既有资产诊断 | help-images.log |
| 换行检查 | 原始 diff --check 返回 2；cr-at-eol 比较为 0 | diff-check.log、diff-check-crlf.log |

主要命令沿用 [首次验收](CHARACTER_R4A_ACCEPTANCE.md) 所列；本轮共享测试精确为 `node --test --test-concurrency=1 authoring/controlled-provider.test.mjs authoring/model-control.test.mjs`。旧 144 项共享全套的历史结果保留，本次没有冒充重跑完整共享全套。构建分别在父 `client/` 与 `card-replica/` 执行。没有跑全仓后端测试、远端 CI 或新增供应商测试。

## 失败归因和未验证项

- 既有 `a6563a9b` 世界三项：`AUTHORING_RUNTIME_BINDING_MISMATCH`、`OFFLINE_SESSION_CORRUPT`、`JOINT_SESSION_CORRUPT`。原文件与断言未变，未添加兼容豁免。
- 既有帮助三项：语音文章基线不一致、旧文章 11 步超过 8 步上限、旧截图路径与文章基线不一致。截图脚本原有 JPG/尺寸/孤立资产 24 条诊断继续保留，不删除旧资产或放宽检查。
- 本次独立副本初轮未复制引擎 node_modules，出现缺失 handlebars，影响第一次角色、最新存档、网关单项及预览启动。核对 package/lock 完全一致后复制既有依赖，相关失败重新验证通过。未修改项目依赖或安装新版。初轮日志全部保留。
- 本次移动帮助脚本采用候选后过早读取 DOM，误把前一条已有回复当新回复完成；改为等待消息数量增加再比较刷新结果，两种宽度通过。只修临时验收脚本，没有产品补丁。首次 failure.json 保留。
- R3 共享测试首次失败与中断，R4 初期夹具、容量、哈希、GBK/命令环境、等待、截图大小及导航首次失败继续留在历史报告和原目录，不能用最终局部通过覆盖。
- CRLF 差异只做明确归因，没有全仓换行格式化或关闭检查。未发现本轮新增功能回归失败；存在上述既有失败及已解释环境/脚本首次失败。
- 设备延期：原生文件选择器取消、真机软键盘/触控/系统返回。真实刷新仅证实此前 DeepSeek 的一次处理中完整刷新；其他供应商、断网和进程崩溃组合仍待验。
- 模型叙事偏差与系统验收分列，用户已接受，不再追加提示词补丁。本轮短交互不证明极限长度或长期人格一致性。

## Help Center Impact

新增 `control-character-context` 指南、目录登记及专用基线/检索断言。验证基线固定 `aebda0a1 + 本轮增量`，原指南登记不批量更新。清空浏览器上下文后回放独立中性入口；提交两张直接截图（桌面 960×675、窄屏 780×1688，均 ≤250 KiB），只含原创中性夹具。正式角色、会话、原始响应和本地验收图均不入库。

## 提交范围和回退

候选范围 34 文件：long-context 23、研发文档 6、帮助文章及登记/测试 3、中性 PNG 2；其中现有跟踪文件修改 2，新增 32。无 server、旧运行模块、package/lock、构建产物、日志、素材或存储数据变更。详细白名单见下节；最终 SHA-256 清单和本地 PR 草稿留在独立数据目录。

预览 `http://127.0.0.1:43443/revisions`；配套角色、群聊、旧续写端口 43440/43441/43442。只使用本轮新中性数据副本，不接真实网关。原预览和证据不变。回退仅停止本候选预览及撤销独立入口，保留数据；不迁移或重写旧账本。下一步须获明确提交/推送/建 PR 授权，不能将此报告理解为授权。

## 完整文件白名单

- `client/public/help-center/aebda0a1/character-context-desktop.png`
- `client/public/help-center/aebda0a1/character-context-mobile.png`
- `client/src/content/help-center/articles/control-character-context.md`
- `client/src/content/help-center/helpContent.test.ts`
- `client/src/content/help-center/index.ts`
- `docs/ai-rpg-experiment/CHARACTER_R4A_ACCEPTANCE.md`
- `docs/ai-rpg-experiment/CHARACTER_R4A_BUDGET_HANDOFF.md`
- `docs/ai-rpg-experiment/CHARACTER_R4A_CLOSEOUT.md`
- `docs/ai-rpg-experiment/CHARACTER_R4A_HANDOFF.md`
- `docs/ai-rpg-experiment/CHARACTER_R4A_PLAN.md`
- `docs/ai-rpg-experiment/CHARACTER_R4A_REAL_ACCEPTANCE.md`
- `experiments/ai-rpg-engine/character/long-context/application.mjs`
- `experiments/ai-rpg-engine/character/long-context/attach.mjs`
- `experiments/ai-rpg-engine/character/long-context/browser.mjs`
- `experiments/ai-rpg-engine/character/long-context/budget.mjs`
- `experiments/ai-rpg-engine/character/long-context/budget.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/composition.mjs`
- `experiments/ai-rpg-engine/character/long-context/composition.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/controlled.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/handoff.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/launch.mjs`
- `experiments/ai-rpg-engine/character/long-context/legacy-budget.mjs`
- `experiments/ai-rpg-engine/character/long-context/page.css`
- `experiments/ai-rpg-engine/character/long-context/page.html`
- `experiments/ai-rpg-engine/character/long-context/parameters.mjs`
- `experiments/ai-rpg-engine/character/long-context/planner.mjs`
- `experiments/ai-rpg-engine/character/long-context/planner.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/request.mjs`
- `experiments/ai-rpg-engine/character/long-context/server.mjs`
- `experiments/ai-rpg-engine/character/long-context/server.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/sources.mjs`
- `experiments/ai-rpg-engine/character/long-context/sources.test.mjs`
- `experiments/ai-rpg-engine/character/long-context/store.mjs`
- `experiments/ai-rpg-engine/character/long-context/store.test.mjs`
