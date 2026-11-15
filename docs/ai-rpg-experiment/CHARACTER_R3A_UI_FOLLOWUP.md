# R3a 四项页面验收补充记录

日期：2026-11-14。基线和实现分支不变：2a7dfea1 / codex/rpg-character-r3a。此文补充前次报告，不修改或替代原验收日志。

## 结论

浏览器窗口与 URL 只读观察恢复，但 native click 返回 `coordinate input geometry is unavailable`。未再次使用坐标绕过，也未操作 Codex 应用内的页面。因此本轮完成离线回归与必要修复，实际页面门禁仍待验收，不将 DOM 测试称为浏览器通过。

独立验收地址：http://127.0.0.1:43154/characters?desk=profiles；群聊：http://127.0.0.1:43155/groups。
数据与新增证据：C:/tmp/mm-cr3a-data/ui-acceptance-20261114；本轮离线日志在其 offline-followup 子目录。旧 43150/43151 数据未改写。本轮没有重启、部署共享服务或调用产品模型，没有提交/推送。

## 四项覆盖

| 验收项 | 本轮离线结果 | 仍需实际页面检查 |
| --- | --- | --- |
| 未保存档案保护 | 拒绝切换保留称呼、中文多行描述与头像状态；接受放弃后清空；beforeunload 被取消；保存中锁定编辑/导航；冲突保留草稿；权威成功响应后才清除 dirty；重复保存不重复派发 | 原生确认框取消/确认、浏览器刷新/关闭提示、移动端返回操作 |
| 头像上传 | PNG/JPEG/WebP 经解码桩转换为 PNG；512px 缩放参数；只在档案保存时关联；超限/错误类型/动画标志/解码失败/上传失败保留旧头像；取消选择不写入；完成后清空选择值，可重新选择同一文件；真实存储验证 PNG/CRC/解压 | 系统文件选择器；真实图片解码及缩放视觉；同文件重新选择；设备取消选择行为。canvas/createImageBitmap 的前端测试是桩，不能证明实际浏览器解码 |
| 身份快照 | 创建旧单聊/群聊→更新档案并移除头像→创建新会话→重开存储；旧会话对象不变，新会话固定新修订，旧头像资源仍可读；公开身份进上下文，头像摘要不进上下文或导出；新增快照用例无生成请求 | 从页面选择档案、比较旧/新会话显示、刷新恢复与版本标签 |
| 移动端完整流程 | 390px innerWidth 条件下，单角色预览和创建发送精确档案引用；群创建草稿恢复身份/场景/作品版本；创建仅一次；成员抽屉关闭后焦点返回；长中文未发送草稿刷新保留 | 390×844 实际布局、滚动、点击目标、键盘遮挡与跨页导航。JSDOM 不计算视觉布局，不证明 CSS 无溢出或真实触控可用 |

## 最小修复

仅修改生产文件 `character/daily/page.mjs`：

1. 原来 run 仅禁用按钮，保存中还能编辑文本；返回的旧输入可能覆盖新输入。现在同时锁定 input/textarea/select，结束后恢复原禁用状态，保留 pending 会话按钮限制。
2. 上传解码期间 busy 但尚未 dirty 时，原 beforeunload 不保护离页；现在 busy 期间也保护，并阻止站内导航。添加 aria-busy，不更改数据合同。
3. 解码失败改为可操作中文提示，保留原头像；绑定 createImageBitmap 的 Window 接收者。所有上传结束路径清空文件选择值，支持同文件重新选择；bitmap 仍在 finally 释放。

未修改头像存储格式、身份合同、会话实例、上下文装配、运行指纹、世界或 R1 模块；没有为适配测试而放宽验证。

## 回归与失败保留

- `page-before.log`：修复前 13 项中 10 通过、3 失败，复现保存中可编辑、解码错误反馈和上传期间离页保护缺口。
- `page-fixed.log`：当时相关 14/14 通过。
- `flows-first.log`：43/44；新增单角色测试使用了不存在的 start-session 按钮 ID。核对实际 HTML 后改为 start-chat；未修改产品代码来迎合错误测试。
- `targeted-final.log`：全部 daily 测试、单角色编辑器交互、群聊交互合计 **58/58 通过**。包含精确合并基线恢复/漂移拒绝与旧交互回归。
- `preservation-check.json`：64 份旧数据和日志摘要全部一致；本轮既有文件差异仅 page.mjs 和两份交互测试。
- `git diff --check` 通过，暂存区为空。前轮完整回归的三项世界失败、三项帮助失败、24 项旧截图诊断和首次环境失败仍按原报告保留；本轮未重新运行这些无关检查，不能声称已修复。

运行方式（仓库根目录）：

```powershell
$env:CHARACTER_R2_TEST_ROOT='C:/tmp/mm-cr3a-data/ui-acceptance-20261114/offline-followup/tests'
$env:CHARACTER_R3A_TEST_ROOT=$env:CHARACTER_R2_TEST_ROOT
node --test --test-concurrency=3 experiments/ai-rpg-engine/character/daily/*.test.mjs experiments/ai-rpg-engine/character/editor.test.mjs experiments/ai-rpg-engine/character/group/browser.test.mjs
```

本轮只修改直接由宿主读取的静态页面脚本和测试，没有改变父前端/聊天前端构建输入；两个构建仍引用前轮通过证据，没有无意义地重跑。

## 待补页面验收步骤与 R3b 条件

1. 在独立地址保存中性玩家档案和 PNG 头像；编辑称呼/描述，切换档案及离页时分别取消，检查内容仍在。再确认放弃，检查回到已保存数据。
2. 分别上传本地 JPEG/WebP，保存并刷新；替换/移除头像，核对预览；试无效文件和取消文件选择；再次选择同一文件。
3. 用修订 A 建立单聊和两角色群聊，只采用预设开场，不发送模型请求。修改档案为 B，建立新会话；旧会话保持 A，新会话采用 B，刷新后不变。
4. 在真实浏览器 390×844 下重复档案→角色库→单聊/群聊→目录导航，检查长中文、焦点、滚动、成员抽屉、草稿刷新及软键盘遮挡。

进入 R3b 前，仍需完成上述页面人工验收、修复发现的问题并通过相关回归；保留未解释失败的隔离说明。随后单独确认编辑/重生成/候选/分支的原文与回执保留、身份/模板继承及记忆失效规则。真实模型验收仍需单独授权；此次不启动 R3b。

## 本轮增量文件及回退

- character/daily/page.mjs：三项最小修复。
- character/daily/page-acceptance.test.mjs：档案与头像交互回归。
- character/daily/identity-acceptance.test.mjs：跨宿主、头像和重启身份快照回归。
- character/editor.test.mjs、character/group/browser.test.mjs：档案引用与窄屏交互路径。
- 本补充文档。

新增两份测试文件只使用原创中性数据；本地日志、PNG/JPEG/WebP 夹具和会话不入库。回退只撤销上述生产脚本增量，保留本轮数据和证据，不触碰原素材/会话或旧指纹。
