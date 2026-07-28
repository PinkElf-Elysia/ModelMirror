# OpenRouter 模型目录维护

最后更新：2026-07-27

## 数据源与保留策略

前端静态目录 `client/src/data/models.ts` 以仓库中的历史快照为基线，再增量合并 OpenRouter 官方公开接口：

```text
GET https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest
```

目录 ID、名称、上下文、输入输出模态、价格和支持参数以接口当次返回为准。接口未返回的历史条目不会自动删除；疑似失效模型（例如旧 Sourceful: Riverflow）保留供人工验证，仅通过排序规则下置。是否删除条目或改变供应商必须另行审计决定。

## 更新命令

在线更新：

```powershell
node .\scripts\update-openrouter-models.mjs
```

使用已审计的响应文件复现：

```powershell
node .\scripts\update-openrouter-models.mjs --input C:\path\to\openrouter-models.json
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

- 通用聊天与工作流默认：`openai/gpt-5.6-sol`
- Embedding 稳定默认：`text-embedding-3-small`
- OmniRoute 智能路由入口：`auto`

Embedding 下拉框同时展示当前目录中的 Embedding 模型；用户已有但未收录的配置会作为“当前配置”保留，不会在加载页面时被静默覆盖。

## 回退

若新目录导致构建或调用回归，只回退 `client/src/data/models.ts` 和对应默认模型改动，不修改用户会话、RAG 数据、OmniRoute 数据库或 newAPI 配置。
