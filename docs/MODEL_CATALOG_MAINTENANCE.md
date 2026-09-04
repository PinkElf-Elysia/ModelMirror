# OpenRouter 模型目录维护

最后更新：2026-09-03

仓库内可复用流程位于 `.agents/skills/openrouter-update/`。定时任务默认使用其 `audit` 模式：先冻结通用模型、图片、视频和市场侧栏四份公开数据并生成哈希清单，再运行现有目录、分类和 readiness 审计；不读取密钥、不调用收费模型、不修改共享栈，也不自动提交或创建 PR。只有明确要求更新时才进入分批 `update` 模式。

## 数据源与保留策略

前端静态目录 `client/src/data/models.ts` 以仓库中的历史快照为基线，再增量合并 OpenRouter 官方公开接口：

```text
GET https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000
```

目录 ID、名称、上下文、输入输出模态、价格和支持参数以接口当次返回为准。接口未返回的历史条目不会自动删除；疑似失效模型（例如旧 Sourceful: Riverflow）保留供人工验证，仅通过排序规则下置。是否删除条目或改变供应商必须另行审计决定。

## 更新命令

优先按照 `openrouter-update` Skill 冻结并审计输入；该 Skill 的 `SKILL.md`
提供可复制的四源抓取、manifest 校验和完整审计命令。更新必须使用 manifest
所在目录里的冻结 `models.json`，并依次执行 missing、Batch、metadata、lifecycle
和 market 五个阶段，每阶段后重跑完整审计。

示例（假设 `$snapshot` 是 Skill 已校验的冻结目录）：

```powershell
node .\scripts\update-openrouter-models.mjs --input "$snapshot\models.json" --missing-only
node .\scripts\update-openrouter-models.mjs --input "$snapshot\models.json" --batch-only
node .\scripts\update-openrouter-models.mjs --input "$snapshot\models.json" --metadata-only
node .\scripts\update-openrouter-models.mjs --input "$snapshot\models.json" --lifecycle-only
node .\scripts\update-openrouter-market-filters.mjs --input "$snapshot\market.json"
```

不带 `--input` 的在线 full updater 仅供明确授权的人工应急诊断，不属于定时任务
或受审计更新流程：

```powershell
node .\scripts\update-openrouter-models.mjs
```

脚本会机械更新 `rawCatalogModels` 快照，同时保留仓库基线中未被最新接口返回的模型，以及前端分类、能力推断和排序逻辑。更新后至少执行：

```powershell
cd client
npm.cmd run build
```

## 首页排序

模型招聘会默认遵循以下顺序：

1. 当前有效模型优先。
2. `FEATURED_MODEL_IDS` 中经过人工确认的最新旗舰优先。
3. 其余模型按 OpenRouter `created` 从新到旧。
4. Sourceful Riverflow 系列暂时下置，等待人工可用性验证。
5. 已过期条目只在“显示非活跃模型”时出现。

排序只影响展示，不修改模型调用 ID，也不代表平台可用性承诺。

## 默认模型

- 通用聊天默认：`openai/gpt-5.6-sol`
- Agent Builder 与工作流智能体默认：`deepseek/deepseek-v4-flash-0731`
- Embedding 稳定默认：`text-embedding-3-small`
- OmniRoute 智能路由入口：`auto`

Embedding 下拉框同时展示当前目录中的 Embedding 模型；用户已有但未收录的配置会作为“当前配置”保留，不会在加载页面时被静默覆盖。

## 回退

若新目录导致构建或调用回归，优先回退本轮单一提交，使目录、市场侧栏快照、
readiness、专用媒体/音频契约、界面、测试与审计保护保持同一版本。不得只回退
`models.ts` 留下跨文件不一致；也不得修改用户会话、RAG 数据、OmniRoute
数据库、网关密钥或 newAPI 配置。
