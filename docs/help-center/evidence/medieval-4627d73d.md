# 中世纪真实人生：PR 预览与帮助验收

日期：2026-11-13。基线：`origin/main@4627d73d3dbcfab25be6c5df218633999b0d8f54` 加本 PR 差异。

## 范围与前置条件

用户授权提交与创建 PR，不合并、不部署。冻结提示词原文；模型演绎偏差只记录。独立 worktree、仅回环访问的生产前端预览、独立 Linux 容器和数据卷，不接入真实模型通道；本阶段新增真实调用为 0。

初次源码与界面对照、移动端及真实三回合证据见卡片 README 和 LIVE_ACCEPTANCE_2026-11-13.md。原验证基线为 b4080f2c；本记录不会把旧真实调用冒充新基线验证。

## 教程重放

按正式文章 play-medieval-card 的顺序，从新预览来源的空白浏览器状态开始：

1. 故事馆显示 12 部内置作品；搜索“中世纪”并进入，AI 娘和其他内置卡仍在总表。
2. 点击开场，待眼睑/淡出结束后出现六页签。
3. 展开 14 世纪资料，选 1337 百年战争；角色页联动到该世纪/事件。
4. 填马丁、1315、22，选择英格兰王国、自由农、罗马天主教；搜索铁匠并选乡村铁匠。
5. 展开手艺类，锻造设为精通。
6. 抽取八项命运，选择记忆力好、语言天赋、方向感；此生预览汇总全部资料。天赋随机，与真实验收角色不同。
7. 点击“以此角色进入聊天”，得到 645 字符输入草稿、0 回合；刷新后完整草稿保留。
8. 离线发送显示 1 回合已保存；重启独立容器，刷新同一深链接后仍为 1 回合已保存。继续输入“先询问管事的交货期限。”得到第 2 回合已保存。返回故事馆后，“接着上次的故事”显示 2 回合，点击恢复同一会话。

截图由同一预览浏览器实拍，仅转换为 1000px 宽的 PNG 并压缩；没有重绘界面、修改结果或包含地址栏/真实用户数据。角色图展示此生预览，草稿图展示零回合、输入框与离线提示。

- `client/public/help-center/4627d73d/medieval-character.png`
- `client/public/help-center/4627d73d/medieval-draft.png`

文章解释：表单未保存前刷新会丢失、聊天草稿只在浏览器、完成回合在服务端、恢复不调用模型、离线回复不代表模型质量、费用/额度边界和已观察到的演绎偏差。

## 验证命令与结果

- 模块目录：`node --test --test-concurrency=4 medieval-card/card.test.mjs studio/card-entry.test.mjs studio/archive-plugins.test.mjs plugins/mod-panel/mod-panel.test.mjs`，17/17 通过。
- 模块目录：`npm.cmd --prefix card-replica test`，81/81 通过。
- 模块目录：`npm.cmd --prefix card-replica run build`，类型检查及构建通过。
- client：`npm.cmd run build`，通过，保留既有大 chunk 警告。
- client：`npx.cmd vitest run --configLoader runner --maxWorkers=1 --fileParallelism=false src/pages/RpgAuthoringEntry.test.tsx src/pages/RpgWorkspace.test.tsx src/pages/RpgFreeOpening.test.tsx src/pages/RpgLibrary.test.tsx src/content/help-center/helpContent.test.ts`，102/103 通过。四个 RPG 测试文件全部通过，帮助内容 17/18 通过。
- 独立镜像 `modelmirror-medieval-pr:4627d73d` 构建通过，包含卡片前端类型检查与生产构建；独立 Linux 容器重启恢复通过。
- `npm.cmd --prefix client run verify:help-images`：失败 12 处，全部在原始 HEAD 导出的干净帮助文件中以相同命令复现；新增两张 PNG 未产生问题。

## 既有失败与未验证边界

帮助内容唯一失败：manage-rpg-history 元数据为 479cd636，但 regen.png 仍引用 fbe65f12。`git show HEAD:` 已核对该不一致在主线存在，本 PR 不改该文章或削弱检查。

全库图片检查已有 12 项：45ee30b5 两张 JPEG 被 PNG 校验拒绝并错误解释宽度；既有 rpg-published-work.png 宽 1280；五个未引用资产。直接使用 `git archive HEAD client/src/content/help-center client/public/help-center client/scripts/verify-help-images.mjs` 解压后执行原脚本，重现同样 12 项。本 PR 新增截图符合格式/尺寸/体积/引用要求。

初次扩展回归发现的金庸传输快照既有失败见卡片 README；相关旧快照未修改。未运行全仓库后端测试（本次没有修改 Python 后端），未验证生产部署、全部角色时代、长期压缩、崩溃恢复或真实插画。

## 提交内容核对

暂存的系统文本 SHA-256 与冻结来源一致，原 DOCX 共享只读哈希也一致；未追加规则或修改原文。暂存内容的常见凭据格式扫描未发现匹配，没有运行数据、日志、node_modules 或构建产物。

`git diff --cached --check` 仍报告原提示词 65 处行尾空白、三份原字体许可证各一处末尾空行。这些来源文本受原文/哈希保真约束，保留原样并显式登记；排除这四个冻结文本后，其他差异检查通过。没有关闭或削弱测试。

## 回退

撤回本 PR 的卡片目录与注册差异，并恢复上一镜像即可撤销入口。无依赖变更、无数据库迁移；保留中世纪独立数据目录与本地真实验收证据，不清理其他卡片或用户数据。PR 创建后保持未合并、未部署。
