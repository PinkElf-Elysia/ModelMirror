# 肥鱼提示词中间版本：帮助与验证记录

- 日期：2026-11-06（America/Phoenix）。基线：`856a6dd066881c8cef0f89374bdc8d3eadc1c7b0`，从最新 origin/main 建立独立分支 `codex/blue-fish-persona-intermediate`。
- 范围：提示词、来源与完整性断言；帮助中的状态说明、同基线截图及目录元数据。没有改动聊天/编辑器功能代码，也没有部署共享栈。
- 提示词与最后两轮新称呼实测字节完全一致，SHA-256 `deb2b0f61b5b59faa1f0272c14656b0c22db063662090f3b5b5e30b2a972d68a`。

## 离线预览

使用本任务独立前端端口、临时容器文件系统及空数据目录。容器仅启用离线模式，无模型凭据，不挂载共享存档。最新 client 与 card-replica 均重新构建。浏览器为内置预览器，视口约 871×871，截图裁取 850 像素宽的页面内容，无地址栏、凭据或私人数据。

1. 打开故事馆：看到十张卡片，新世界区域显示金庸、斗罗、龙族，创作助手独立显示蓝色大肥鱼。其他卡片只作入口展示，不主张本轮完成其验收。
2. 下滚至创作助手：看到肥鱼头像、名称、简介和“打开助手”入口（无障碍名称“进入蓝色大肥鱼”）。没有存档、没有发送操作。
3. 点击入口：路由进入 `/rpg/blue-fish`，标题为蓝色大肥鱼 · 创作助手，但本次离线预览的嵌入聊天区域空白。刷新及打开同源宿主仍未显示；静态脚本字节与最新构建产物相同。原因未定位，不能算聊天 UI 验收通过，也未归因为提示词变更。
4. 重新构建后打开帮助文章，页面实际显示“不稳定中间版本”、过滤中断、费用和旧存档边界及两张新截图；再按文章中的故事馆链接返回，仍可定位创作助手且为零存档。未创建会话、未输入或发送消息；复查时没有遗留草稿或筛选条件。
5. 新帮助内容仅解释中间版本状态、已有真实试验结果与限制，不新增未经验证的聊天操作步骤。完整聊天行为仍待后续验证。

截图 `856a6dd0/rpg-library.png` 和 `856a6dd0/blue-fish-entry.png` 来自上述本次页面；更换文章基线时更新固定测试断言并替换已无引用的旧故事馆图片，保留其他文章资产。PNG 按帮助规范保存并压缩，不改变界面布局。

## 验证结果

- 临时无外网容器内，`node --test blue-fish-card/blue-fish.test.mjs blue-fish-card/transport-parity.test.mjs plugins/custom-config/custom-config.test.mjs`：10 通过。使用本地模拟请求，非真实模型证据。
- `npm --prefix client run build`：通过（保留既有大包警告）。首次因新工作树缺少 card-replica 依赖路径失败；补齐与原工作树相同锁文件依赖后通过，没有更改锁文件。
- `npm --prefix experiments/ai-rpg-engine/card-replica run build -- --base=/rpg-app/earth/`：通过。
- 前端 BlueFishCard、RpgFreeOpening：24 项通过。
- helpContent、HelpCenterPage：74 项通过。首次因文章固定基线和图片归属变化失败；已更新真实截图及对应精确断言，没有放宽校验。
- `npm --prefix client run verify:help-images`：通过。旧故事馆截图因替换后不再引用已移除，仍可从 Git 历史恢复。
- 完整 `git diff --check` 会报告冻结 system.txt 自带行尾空格；为保持用户批准及实测字节，未裁剪该文件。其他候选文本格式检查通过；这不是宣称完整 diff check 通过。
- 本次发布收尾没有追加 Gemini 请求。过往最终版本一轮问候成功、复测问候 content_filter、世界书成功，见 BLUE_FISH_CARD.md；不能证明重复稳定性。

## 交付限制与回退

草稿 PR 标注“不稳定中间版本，等待后续优化”。不合并、不部署，不迁移旧会话。回退提示词时同步恢复 system.txt、source.json 和完整性断言；保留用户存档。原始请求、响应、SSE、临时服务与测试目录不纳入 PR。
