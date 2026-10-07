# OpenRouter 更新帮助与独立预览证据

- 日期：2026-10-07；基线：c568ccf5，功能提交：0dc4fe1c。
- 独立前端：http://127.0.0.1:5199；未迁移凭据、未部署共享栈。
- 按帮助路径在 /models 搜索 openai/gpt-6-luna-decisions，只出现目标卡片，显示“创建决策”、文字/图片目录能力及图片状态尚未验收提示。
- 点击“创建决策”，实际进入 /decisions/openai%2Fgpt-6-luna-decisions。可见状态、类型化问题 JSON、/api/alpha/decisions 契约和费用预估；状态为空时提交与复制请求禁用。未提交请求。
- 模型页实际可见本轮 5 个指定模型；无后端配置的前端预览中，三个媒体模型显示“交互待适配”及价格/契约说明。仅证实展示和门禁，未证明媒体生成成功。
- 帮助内容：client/src/content/help-center/articles/check-availability-cost-data.md，补充决策入口操作和媒体计价/验证边界。
- 未验证：真实 Provider、付费调用、图片上传、视频完成、共享栈集成。不会将静态契约与 mock 测试标记为真实验收。
