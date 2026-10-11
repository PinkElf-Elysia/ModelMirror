# OpenRouter 2026-10-10 更新

## 范围与证据

- 独立工作树 `C:/tmp/modelmirror-openrouter-oct10`，基底 `e5a808a6`（#409）。不修改共享栈、默认模型或凭据。
- 四源冻结清单 `C:/tmp/openrouter-oct10-window/manifest.json`，UTC 2026-10-11 05:48:31–05:48:33（Phoenix 2026-10-10 22:48）。
- 初始审计 `openrouter-oct10-before` 因历史 readiness 计数随到期时间失配而不完整；校正计数后 `openrouter-oct10-before-counts/summary.json` 为有效初始证据。
- 分阶段审计：missing-r2、batch、metadata、lifecycle、market，均位于同名 `C:/tmp/openrouter-oct10-*` 外部目录。最终 market summary 为 clean，结构化与易变市场漂移均为零。

## 更新

- 指定模型：Microsoft Decision 1、Clef Omni、Drex v1.5、Step 5 Preview。
- 漏项补齐：`inception/mercury-decide`、`inclusionai/ling-3.0-flash-sante`。付费版不替换 free 身份。
- Batch：GPT-6.1 Sol / Sol Pro 两个服务档位，只在基础模型设置中使用，不增加卡片与模型计数。
- 45 条元数据漂移：34 价格、7 到期日期、7 参数、4 上下文、1 描述（字段间重叠）。同步侧栏结构与易变观测。
- 缺失的 Sante free 保留可进入入口并标为可能不可用，不删除历史记录。主线已到期条目按明确日期计算，目录中的更新到期日期优先。
- 新模型通过既有第六排后稳定分散排序，不更换旗舰或默认模型。

## 契约与限制

- 三个指定决策模型及 Mercury Decide 复用 `/api/alpha/decisions`，不是普通聊天。本站文字 state、最多 50 题、noul/choice/score 与现有边界保持一致。
- 官方来源：对应 OpenRouter 模型页面、canonical endpoints 和 [Decisions API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)。Drex 上游的 `/v1/systemone` 不等于 OpenRouter 请求地址。
- Clef Omni 官方支持文字和图片，音视频尚未开放；本站本轮仅完成文字决策。图片状态格式和界面仍未适配，明确提示，不宣称全模态完成。
- Step 5：文字/图片/视频理解，100 万上下文，输入 $1/M、输出 $2.7/M；推理模型，复用既有聊天能力。未执行真实调用，不证明网关可用性。
- 本轮无付费调用、配置复制、提交、推送或 PR；独立前端预览使用 5299，代理指向未启动的 5298，不连接共享后端。

## 计数

源目录 659 = 579 非 Batch + 80 Batch；市场 579 个唯一基础 ID，与非 Batch 集合完全一致。
本地保留快照 700 = 579 live + 96 uncertain + 25 expired；站内适配分母 675。Batch 不计数；这些数量不代表真实可调用数量。

## 验证与回退

- 后端 Decisions、Batch、Managed Batch：41 passed（4 条既有 FastAPI deprecation warning）。
- 前端 `models.refresh`、`models.oct10`、`openrouterBatch`、`OpenRouterBatchWorkspace`：84 passed。针对快照、排序、生命周期与 Batch 逐项保留断言；旧价格与目录断言按冻结数据更新，不跳过测试。
- Node 更新器、价格契约、inspect-json、stable-signature：22 passed；`npm.cmd run build` 通过，仅现有大包警告；`git diff --check` 通过。
- 收尾复核 `C:/tmp/openrouter-oct10-final/summary.json`：clean，同一冻结窗口，结构与易变市场漂移均为零。
- 发布前第二窗口：`C:/tmp/openrouter-oct10-pr-window/manifest.json`，UTC 2026-10-11 07:11:56–07:11:58。新增 4 项价格漂移及 4 项结构化市场漂移已修正，并同步 142 项工具成功率观测；`C:/tmp/openrouter-oct10-pr-clean/summary.json` 为 clean。第二窗口修正后前端上述 84 项测试再次通过。
- 独立预览已观察 Microsoft Decision 1 和 Clef Omni 从市场进入专用决策页，展示类型化问题、契约、费用，空状态提交禁用；Clef 卡片明确图片未适配。未发送请求。
- 浏览器自动保存截图曾被 EPERM/EACCES 阻断；用户随后提供同一预览的 Clef 和 Sol Batch 截图，已纳入注册帮助文章。清空搜索后重放决策和设置内 Batch 入口，确认契约、费用、24h 窗口与 30 天保留提示；详见 `docs/help-center/openrouter-oct10-preview.md`。
- Clef 图片、真实供应商和全量后端尚未验证；用户提供截图不等于付费调用验收。
- 帮助中心文章及一级索引补齐后，5 个前端文件共 100 项测试通过；截图资产检查通过，生产构建通过（既有大包警告）。
- 回退只撤销本轮目录、参数白名单与测试文档改动，无数据库或环境迁移。
