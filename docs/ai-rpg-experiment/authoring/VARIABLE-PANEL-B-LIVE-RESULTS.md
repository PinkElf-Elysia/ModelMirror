# 变量面板 B 组真实联动验收

日期：2026-11-05。用户明确授权两轮、共四次调用。技术及内容检查通过。后续交付记录：用户确认本批收尾并授权提交PR，未新增调用；以下原始结果和验证边界保持不变。

## 本批范围

- 工作树 `codex/variable-preview-layout`，基线 `6aa5924dd64b706a684ab2fd0dcc6da1d9feeb08`。
- 使用冻结的“雾港灯塔”作品包；包 SHA-256 `4c05aba8ad02117ad333f42cfb6152aec994051b92ec15530bccc3c1cf544c11`。通过界面导入、发布本地版本、创建新会话及配置模型与插件，没有修改作品内容或运行代码。
- JP 控制面既有 RPG 专用路由，经 OpenRouter 使用 `google/gemini-3.8-flash`。剧情参数 `0.70 / 0.80 / 8192`，记忆参数 `0.20 / 0.80 / 16384`；M1=1、M3开启、M2关闭，不保留开局原文。
- 本地新额度 4/4；原控制面 limit 从 10 增至 14，旧 spent=10 保留，结束 spent=14。没有认证、自动重试、超额调用、共享重建或重启。
- 独立入口：`http://127.0.0.1:18654/?work=work-aad485cd016f1de6de28a7f2b20bf0ddd4c2928b7f08b8b372019b74039a17d1`。
- 保留 18642、18644、18650 和修复预览 18652。最终静态页面均返回200；旧 A 组9份核心记录及1203份既有源码/说明文件 hash 未变。页面200仅作为入口存活证据，功能结果来自下方实际回执和界面检查。

## 两轮结果

| 轮次 | 剧情及变量面板 | 记忆宫殿 |
| --- | --- | --- |
| C1 | 完成灯塔巡查并拾取铜钥匙；地点北岸灯塔、体力70、铜币20、背包航海图及铜钥匙、发现=true、交付=false、备注=null。状态 applied，revision=1。 | 原始完整回合（含变量快照）作为来源；新建1条巡查与获取钥匙记忆，保留尚未交付的信息。 |
| C2 | 正确复述钥匙在北岸灯塔灯室操作台角落取得；没有再次走路、拾取或交付。状态 unchanged，全部状态值、dataHash和revision保持。 | 第1回合历史关键词命中，实际携带该条目version1、正文117字符。整理合法返回 `operations: []`；处理到第2回合，条目没有重复新增。 |

四份回执均为 HTTP200、completed、finishReason=stop、retries=0，实际模型与冻结参数一致。剧情两任务 completed，记忆两任务 applied，无未知、待派发或待收束任务。

两轮状态 dataHash 均为 `e7959381c5c24095605c943c00894ce004e2aec6ea3fe4e0cc9b3612ee6b9a1f`。C1 与 C2完整面板 snapshot相同，revision仍为1。记忆库revision从1推进至2，已有条目version仍为1。

用量回执合计：输入3267、输出2443、总计5710 tokens；不将此视为美元结算凭证。

## 语义核对与边界

- 剧情对巡查补充了齿轮、透镜等叙事细节，没有追加交付、奖励或额外消耗。
- 记忆忠实概括该轮原文，但没有记录体力和铜币数值，因此不作为完整状态备份。
- 第二轮当前输入没有命中所生成的关键词；召回来自第一轮用户与助手原文，符合独立历史扫描规则。
- 第一轮仍在 M1 最近1回合发送窗口内。此结果证明变量状态与M3同轮共存及合法无变更推进，不证明窗口外长期记忆召回；本批未覆盖M2或M2+M3联合真实调用。
- 内容评价由助手逐份核对，未代替用户宣布人工质量验收通过。

## 验证和界面证据

- 发送前：`node --test authoring/controlled-provider.test.mjs authoring/memory-jobs.test.mjs authoring/memory-state.test.mjs authoring/variable-runtime.test.mjs`：79通过，0失败；范围受限转发器的21项离线身份/参数/重复/额度检查通过，无模型调用。
- 每轮实际请求与回执、剧情落盘原文、面板快照、记忆来源、条目版本分别核对；两份结构报告各8项通过。C2只在C1结果和内容检查通过后从界面发送。
- 浏览器刷新后恢复2回合和已处理2回合的记忆；查看状态JSON与模板值一致；“复制地点”实际返回“北岸灯塔”。
- 390×844与1280×720切换核对：顶部模型、刷新、关闭、插件、记忆、记录按钮均无相互重叠，无文档横向溢出。已清除临时尺寸覆盖。
- 三份真实截图：`captures/desktop-result.png`、`captures/mobile-result.png`、`captures/memory-result.png`。全部保存在本批本地证据目录；未提交运行产物。
- 本批没有生产代码变更，因此未重复全量构建。最终 `git diff --check` 通过，暂存区为空；既有53项工作树变更保持，此批仅新增本结果说明。

## 证据定位和回退

本地证据根：`experiments/ai-rpg-engine/.rpg04-work/variable-panel-B-live-20261105/`。

- `authorization.json`、`plan.json`、`harbor-panel.package.json`：授权与冻结输入。
- `allocation.json`、`route-after.json`：控制面路由、参数验证及前后额度。
- `C1/C2.story/memory.claim.json`、`.request.json`、`.returned.json`：四个独立操作及实际请求/回执。文件名的斜线表示对应组合，并非真实子目录。
- `C1.review.json`、`C2.review.json` 和各自 `.semantic-review.json`：结构与内容检查分开保存。
- `data/sessions/records/`、`data/joint/budget/records/allowance.json`：持久会话、后台任务与实际联合预算。
- `final-verification.json`、`evidence-sha256.json`：最终检查及本地证据hash清单。

本批已写入 `closed.json` 关闭新派发，只保留查看与回放；没有发送第五次调用。需要停止验收时，仅关闭18654/18655对应的本批进程并保留全部文件。不得删除已使用额度或改写旧A组记录。

未Commit、Push、PR、Merge或共享部署。仅本地发布验收作品版本；未将新作品分发至远端。
