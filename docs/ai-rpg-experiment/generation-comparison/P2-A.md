# P2-A 创作端参数编辑闭环

状态：本批实现和离线验收完成，等待用户验收；不代表全部 P2 完成。基线 d9f20fe691e8ddef8623d779a25a9359717f2e8d；独立 codex/rpg-generation-comparison 工作区。

## 任务卡

- 目标：创作工作台以原生界面使用 P1 的类型化参数能力，按剧情／摘要／记忆独立应用到当前正式会话；保存不生成。
- 允许文件：authoring/model-ui.mjs、model-ui.css、joint-ui.mjs（仅修复刷新覆盖入口名称）、新增 parameter-ui.test.mjs、本文件。若出现必要接口缺口先补充范围理由。
- 证据：P0 已批准桌面／390px 原型；P1-C 正式会话和控制面预检接口；本次原站实操。
- 禁止：真实调用、认证、共享部署、提交发布、迁移旧会话、修改历史证据、增加依赖。保留原验收服务和数据。
- 验收：类型草稿、能力冲突、三用途隔离和真实 HTTP 配置／回执；浏览器模型搜索、默认／显式、数值／滑杆、停止词、思考依赖、取消／Esc、刷新恢复、多标签冲突及390px/回桌面。全部使用本地假控制面。
- 回退：恢复本批 UI 文件即可；已保存的 v2 配置仍由 P1 合同读取，历史和账本保留。
- 本批不声明：预设／推荐配置 UI、聊天端参数插件、六卡接线、对比生成或真实 Provider 验收。

## 原站观察与本地约定

2026-11-06 再次实际打开测试作品 bdd69f55…：添加模型打开参数浮层，温度／Top P／Top K／惩罚／长度／思考预算均为滑杆和即时数值；点击当前模型打开搜索与分类浮层，输入不存在的名称过滤列表（当前模型项仍保留）；Esc 逐层返回并最终关闭。没有保存作品或发送请求。

原站显示“更换模型后参数重置”的说明。本地按已批准 P0 合同保留参数、列出不兼容项；三用途、明确应用与放弃确认、手机全屏均为本地设计。参数范围及用途来自受控目录，不照抄原站通用滑杆范围。

## 验收结果

- 新增 `parameter-ui.test.mjs`：17/17 通过，0 跳过，最终 20.17 秒。覆盖 default/explicit、零值、空值、类型／范围、互斥／依赖、切换模型保留冲突、能力变化、三用途保存、重复操作、重启核对、多标签冲突及受控请求捕获。
- 受影响旧回归 `model-ui.test.mjs`、`history-ui.test.mjs`：33/33 通过，263.25 秒。两组分别运行，不将局部用例称为完整参数阶段验收。
- 浏览器实际核验：目录搜索／空结果、滑杆和输入联动、停止词新增／删除及首尾空格、基础／高级折叠、思考开关与预算、无效值阻止应用、用途草稿切换、取消与逐层 Esc、未应用修改确认、保存刷新恢复。
- 多标签：第二页修改后，第一页以旧 revision 应用被拒绝，编辑值保留；主动刷新核对后可应用。最终恢复剧情温度 0.9，摘要 0.2、记忆 0.1，三个用途分别保存，未授权任何插件。
- 桌面 1280×720、390×844 和 390×520 短视口检查通过；底部操作在可视区内，没有横向溢出；切回桌面按钮没有重叠。短视口仅模拟可用视口缩小，不宣称真实手机软键盘已实机验证。
- 两处前端构建通过；主前端保留大包体积警告。JavaScript 语法和 `git diff --check` 通过。P1-C manifest 的 15 份文件 hash 全部保持一致。
- 当前浏览器验收会话：6 次模型配置修订，正式剧情 0 回合，生成操作 0；本地假控制面任务 0。所有参数预检／应用只读目录和写入会话配置，真实供应商调用为 0。

## 实现与操作

1. 创作调试区点击“模型与参数”，剧情／摘要／记忆分别选择模型。入口不依赖创作端的模型选择插件开关。
2. 默认值与显式值独立保存；勾选“显式设置”再编辑。有效默认值由能力目录声明；未声明默认值的字段显示“不传此参数”。高级字段按当前模型能力显示，不伪造支持。
3. 切换模型保留原编辑。范围、互斥、依赖及不支持的字段会明确阻断应用；不兼容字段须明确移除。预检不保存、不生成。
4. “应用到会话”核对控制面后保存当前用途；另两个用途、作品版本和原会话历史保持独立。配置写入结果未知时保留原操作，按原 ID 核对。只读预检失败不会创建待恢复写操作。
5. 已有 v1 配置保留；在新版面板中须明确选择模型才进入 v2 编辑，不静默迁移旧记录。关闭面板时未应用草稿需要明确放弃。

## 过程问题与实际边界

- 首次桌面浏览器检查发现底部操作超出可视区，补充确定的面板高度和内部滚动后复测通过。
- 多标签配置写入期间，背景会话面板曾短暂显示待核对。关闭参数面板刷新后清除；控制面捕获证明没有生成操作。样例关闭生成执行开关，因此输入预检显示“模型调试服务尚未开启”，属于本批离线环境状态。
- 最终清理测试导入时误删了仍使用的断言辅助函数，出现 `ReferenceError: code is not defined`；已恢复该函数，保留 `test-cleanup-failure.log`，整组 17 项重新通过，没有修改生产规则或弱化断言。
- 首次构建缺少已锁定的前端依赖，按原 lockfile 执行 `npm ci --ignore-scripts --no-audit --no-fund` 后两处构建成功；锁文件没有变化。
- 预设／作品推荐配置 UI、聊天参数插件、六卡宿主接线属于后续子批。当前样例采用合成模型能力和本地假控制面，不代表 Gemini 等真实模型的支持范围、资格、费用或生成质量。

## 文件与证据

本批五个文件：

- `experiments/ai-rpg-engine/authoring/model-ui.mjs`：类型化草稿、三用途编辑、预检与应用恢复。
- `experiments/ai-rpg-engine/authoring/model-ui.css`：创作参数面板及手机布局。
- `experiments/ai-rpg-engine/authoring/joint-ui.mjs`：受控创作入口固定为“模型与参数”，防止刷新被旧模型名覆盖。
- `experiments/ai-rpg-engine/authoring/parameter-ui.test.mjs`：纯逻辑、正式 HTTP、请求捕获及独立离线浏览器样例。
- 本文：任务卡、观察事实、验证范围和回退。

验收入口：[18662 参数工作台](http://127.0.0.1:18662/?work=work-ff9fd0893c5f1937857dd78a52b861998b42a086b11869fa47fba66b55c4ac88)。旧入口、共享栈和真实账本未变更。

外置证据目录 `C:/tmp/modelmirror-rpg-p2a-evidence`：

- `verification-summary.json`、`parameter-ui-test.log`、`client-build.log`、`rpg-build.log`。
- `http-evidence.json`、`request-evidence.json`：三用途配置与实际请求／Python 参数映射／模拟供应商回执。
- `browser-session.json`、`browser-control-capture.json`：界面保存结果与零生成记录。
- `p2a-desktop.jpg`、`p2a-mobile.jpg`、`p2a-mobile-short.jpg`、`p2a-conflict.jpg`。
- `protected-p1c.json`、`P2-A-HASHES.json`：前批保护检查和本批源码／证据 hash。

复现命令（仓库根目录）：

```powershell
$env:RPG_TEST_PYTHON='C:/tmp/modelmirror-gate-q-venv-4764406c/Scripts/python.exe'
node --test --test-concurrency=1 experiments/ai-rpg-engine/authoring/parameter-ui.test.mjs
node --test --test-concurrency=1 experiments/ai-rpg-engine/authoring/model-ui.test.mjs experiments/ai-rpg-engine/authoring/history-ui.test.mjs
npm.cmd --prefix client run build
npm.cmd --prefix experiments/ai-rpg-engine run build
git diff --check
```

未 Commit、Push、PR 或共享部署；没有认证或真实模型调用。回退只撤回本批界面接线，保留 P1 的参数配置与回执、所有会话及账本。下一批在用户验收后补齐预设与作品推荐配置，再完成聊天端接线。
