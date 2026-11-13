# 故事馆统一目录与本地发布验收

- 日期：2026-11-06。
- 基线：`45ee30b5`，本轮未提交工作树 `codex/rpg-library-publishing`。不是已提交版本或 CI 结果。
- 环境：独立临时作品库、本机回环前端与工作台；固定离线 Provider，额度为 0。未连接共享栈或真实供应商。
- 对应帮助：`play-rpg-cards`、`publish-rpg-work`，以及本地创作指南的版本章节。

## 可见操作与结果

1. 故事馆显示 10 个原有入口，全部使用同一网格、封面比例、简介区和操作区；缺图显示文字占位。原有简介、图片、作者提示词、卡片路由均保留。
2. 从首页进入工作台，新建中性虚构作品“雾港来信”，填写面向群体、提示词、开局和简介，不上传封面。保存后目录仍为 10 个入口。
3. 通过“作品版本 → 发布当前草稿 → 发布版本”发布本地第一版。返回首页后自动出现第 11 个入口，无需修改代码。
4. 打开入口进入只读的已发布游玩页面。开局显示“雨后清晨，一封信抵达港口。”。创建第一版空会话后，界面明确显示未发送模型请求，刷新仍显示 0 回合。
5. 从“编辑作品”修改测试作品简介并保存；另一首页标签页刷新后仍显示第一版旧简介。再次发布后该入口显示第二版和新简介，入口总数仍为 11。
6. 第二版入口要求创建会话，没有自动选中第一版会话；“作品版本”第一版的“打开此版本”仍恢复原有空会话与玩家输入。
7. 首页在 390px 视口为单列，卡片宽 344px，页面宽 380px，无水平溢出。桌面为三列。游玩区采用可滚动布局，避免工具区挤压开局内容。

截图：

- `client/public/help-center/45ee30b5/rpg-library-unified.png`：第二版发布后统一目录。
- `client/public/help-center/45ee30b5/rpg-published-work.png`：第一版只读游玩页、空会话和固定开局。

截图中内置卡片“存档暂不可用”源于隔离预览没有接入内置卡片运行服务；不能据此判断共享栈异常。本轮未使用真实存档。

## 自动验证

在 `experiments/ai-rpg-engine` 执行：

```powershell
node --test --test-concurrency=2 authoring/session-selection.test.mjs authoring/library.test.mjs authoring/store.test.mjs authoring/server.test.mjs authoring/model-ui.test.mjs authoring/playback-ui.test.mjs authoring/creator-experience.test.mjs authoring/joint-client.test.mjs authoring/presentation.test.mjs
```

相关回归 106 项通过，覆盖草稿不可见、重复发布、不可变快照/封面、发布预览篡改拒绝、跨源边界、版本会话引用与待确认操作隔离、原存储和展示接口。

在 `client` 执行：

```powershell
npm.cmd run typecheck
npx.cmd vitest run src/pages/RpgLibrary.test.tsx src/pages/RpgPluginsPage.test.tsx src/pages/RpgAuthoringEntry.test.tsx src/content/help-center/helpContent.test.ts
npm.cmd run build
```

类型检查通过；前端与帮助 59 项通过；构建通过，保留既有体积警告。目录测试包含搜索/筛选、焦点刷新、失败保留和恢复、恶意封面 URL 拒绝及缺图占位。

曾启动完整 `test:authoring`，长时间运行到无关分支故障注入用例后停止本轮测试进程；未完成全套验收，不将上述局部结果计为全量通过。未修改或降低测试断言；帮助新增文章的基线及一级索引已补齐。

## 兼容与边界

- 原运行指纹文件与存储格式保持不变，没有迁移。发布入口按作品和版本分别保存会话选择、输入草稿和回放引用；原工作台引用保留。
- 新目录只跨回环地址提供展示字段和已发布封面；提示词、未发布草稿、私有资源和修改接口仍受原同源校验。
- 本机发布不是社区发布。配置工作台端口且服务运行时才能读取本地目录。
- 没有追加模型调用、补背景图、改写原简介、提交、PR 或共享栈部署。
- 回退只需还原本轮源码。作品版本仍为原有格式，不删除或回滚用户数据。
