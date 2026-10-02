# OpenRouter 专用模型帮助中心验收（6413a273）

## 验收范围

- 最新远端主线：`origin/main@f819b6cc`（2026-10-01 刷新确认）。
- 叠加基底：待合并 PR #396 的 `6413a273`；本轮变更在独立工作树中验证。
- 独立预览：`http://127.0.0.1:5206`，未复用共享栈、既有预览器、卷或测试数据。
- 受影响入口：`/models`、`/help/review-specialized-models`。

## 前置条件

- 只验证公开目录、静态契约、模型卡片和帮助内容。
- 不读取密钥，不上传文件，不提交图片、语音、决策或聊天请求，不产生付费调用。

## 首轮界面实操

1. 打开 `/models`，确认页面显示 `669 个模型 / 658 已适配`。
2. 逐一搜索以下精确模型 ID：
   - `bytedance-seed/seedream-5-0-flash`
   - `black-forest-labs/flux-3-image`
   - `liquid/d1`
   - `apodex/apodex-1.1-mini:free`
   - `microsoft/mai-voice-2.1-flash`
   - `microsoft/mai-voice-2.1`
   - `unbiased/pareto-26.10-preview`
3. 预期每次只出现对应卡片，并显示正确任务类型、输入能力、入口或待适配状态和价格说明。
4. 实际结果：七个 ID 均可独立检索；Seedream 与 FLUX.3 显示“图片生成/编辑 · 待适配”和专用 Images API 说明，D1 显示结构化决策入口，Apodex 与 Pareto 显示聊天入口，MAI Voice 2.1 两款显示“文字转语音待适配”及 `$15/$22 每百万字符`目录价。

## 清空状态后按教程重放

1. 清空模型市场搜索框，打开 `/help/review-specialized-models`。
2. 确认模型名、逐步操作、费用边界、人工验证边界和两张截图可见。
3. 选择该节中的“模型市场”链接，返回 `/models`。
4. 按教程搜索 `Seedream 5.0 Flash`。
5. 预期看到图片生成待适配状态、文本与图片输入、1K/2K、18 种宽高比、最多 14 张参考图及 `$0.018/张`说明。
6. 实际结果与预期一致；未执行“生成图片”。

## 截图

- `client/public/help-center/6413a273/openrouter-image-model.png`
- `client/public/help-center/6413a273/openrouter-specialized-models.png`

截图不包含真实用户数据、凭据、Token、内部地址或生成内容。

## 未验证边界

- 未验证任一 Provider 的真实可用性、生成质量、首字节时间、最终价格或账单。
- MAI Voice 2.1 两款仍等待最小短音频人工验收；PCM 与 Azure 风格参数未在本轮界面开放。
- 没有向 OpenRouter 或其他第三方发送用户内容。
