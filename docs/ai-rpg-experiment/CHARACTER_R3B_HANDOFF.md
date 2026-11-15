# R3b 合同与交接

日期：2026-11-14。状态：离线实现与页面可用；真实受控生成未接通，发布门禁未完成。本文补充历史 `CHARACTER_R3B_PLAN.md`，不改写计划或原验收记录。

## 基线、隔离和范围

- 工作树 `C:/tmp/mm-cr3b`，分支 `codex/rpg-character-r3b`；开工拉取的主线 `f31bf50ba99686abfb18431413fddd2da16e5648`。
- 组合 R3a 基线 `2a7dfea1e8475ba2654ee4055e1c4cb544028483` 的 50 个白名单文件。与上游增量无同路径冲突；原目录不修改。不能把这 50 个文件当作已经合并的主线内容。
- 数据、日志与脚本在 `C:/tmp/mm-cr3b-data`，预览最终副本 `preview-final-review`。仅中性夹具；不载入正式角色素材，不读取网关凭据。
- R3b 新增 `character/revisions/` 独立 scope；在 R3a 副本上仅额外修改 editor.mjs、group/browser.mjs、daily/page.mjs 的可选导航，以及帮助登记。旧宿主合同、指纹与兼容清单没有 R3b 改动。
- 无新增项目依赖、迁移、提交、推送、部署或产品模型调用。临时浏览器工具复用 `C:/tmp/mm-r3a-pw`。

## 文件和接口

| 文件 | 责任 |
| --- | --- |
| revisions/contracts.mjs | 追加事件、消息修订、分支、候选、参与边界、派生资料、安全投影 |
| revisions/sources.mjs | 经旧宿主验证的单聊/群聊来源、稳定引用、精确版本及证据摘要 |
| revisions/context.mjs | 重生成前文、续聊装配、成员过滤、夹具价格与预留 |
| revisions/store.mjs | SessionRecords、单账本 CAS、幂等、恢复及独立指纹 |
| revisions/host.mjs | 仅离线提供方；意图、派发、核对回执、未知冻结 |
| revisions/coordinator.mjs | 本启动器内原入口与新分支的串行预算协调 |
| revisions/server.mjs | loopback、Host/Origin、Cookie/CSRF、字段及体积限制 |
| revisions/launch.mjs | 三端口独立夹具启动器，无远程提供方开关 |
| revisions/page.html / page.css / browser.mjs | 续写页、编辑草稿、候选选择、原文、导航与导出 |
| revisions/*.test.mjs / test-support.mjs | 原创中性夹具与离线回归 |

内部接口为 RevisionStore.importSource/read/view/list/mutate/recover，RevisionHost.generate/recover/closePrepared。mutate 动作包括 fork、revise、restore、select、review，以及仅宿主可执行的 prepare/dispatch/settle/unknown/close-prepared。

HTTP GET：`/api/bootstrap`、`view`、`original`、`export`；POST：`import`、`fork`、`revise`、`restore`、`select`、`review`、`generate`、`recover`、`close-prepared`。不公开 settle；浏览器不能提交可信回执、角色定义、完整上下文或网关地址。除 import 的创建合同外，写入携带 familyId、operationId、expectedRevision、payload。恢复使用原请求身份，不接受替换请求正文。

## 有效历史与原证据

1. 编辑玩家、角色或开场正文会创建子分支，只继承到编辑位置；原会话、旧回复与后续均保留。人工正文使用 manual-edit 来源，不伪造模型回执。
2. 显式分支保留到指定消息。成员状态取该消息时的历史边界；后加入成员及未来资料不倒灌。同名成员继续以稳定来源 ID 区分，实际隔离键为 branchId 与 sourceMemberId 的组合，未重映射实体 ID。
3. 重生成只接受可核验的已完成生成上下文；不包含原回复、候选或后续。完成只新增候选，显式选择后才进入有效历史。历史位置采用会产生分支；末尾采用追加选择事件。
4. 原回复仍在对话与“原文与来源”中，可“恢复原回复并分支”；尚未作为统一候选列表的一行呈现。
5. 固定玩家身份快照、头像引用、角色版本和群场景；不读最新档案。续写页展示姓名/描述，未单独渲染头像。
6. 角色书重新计算；有依赖资料随修订失效；无可靠依赖的旧记忆默认待确认，不自动注入。人工确认产生当前分支修订，不改原资料。未调用摘要或记忆模型。
7. 普通导出只包含有效路径和人工来源标注；不包含未选候选、私有资料、凭据或回执。角色卡导出没有引入分支读取。

来源读取先后核对原文件封装哈希，并经过原宿主验证。族记录封存前缀和规范化请求/回执；群网关完整原始 evidence 仍保留在原传输记录中，族中仅有引用/摘要，不能声称原始 HTTP 字节被复制到族内。当前每次加载仍核验原来源，原文件或精确角色版本不可用时阻断；尚不具备脱离原数据目录的便携分支包能力。

## 持久化、预算与恢复

scope 为 `character-revisions-v1`，一个 families 账本原子提交成员边界、分支、候选、请求和额度。相同操作返回原结果，同 ID 异参数及过期修订拒绝；重放时重新装配 prepare 请求，连同上下文核验，重封装伪造哈希也不能绕过。

状态：prepared → dispatching → completed/failed/unknown。先保存意图与预留，再派发。未知保留占用、冻结族写入；查询不到记录不重派。只有 prepared 且无提供方持久化 claim 的请求允许关闭。回执恢复只新增一次候选，不自动选择。

当前只有确定性 RevisionFixture；RevisionHost 拒绝其他提供方，真实额度没有开放。夹具全局授权与族上限同时生效，分支不复制额度；启动器统一包装旧发送和新发送，未知请求阻止旁路。该证明仅覆盖本启动器的离线执行，不覆盖共享真实网关或其他独立进程。

当前硬边界：32 个族、64 个分支、深度 32、每槽 16 个候选、每族 2000 个事件、正文 100000 字符。达到限制拒绝，不删原证据。

## 与计划相比仍待完成的门禁

- **真实受控 R3b 适配未实现/未开放**：需要可信模型描述、价格上界、原入口与分支统一真实授权账本及故障验证。不得只把夹具构造器替换成 HTTP 调用。既有网关合同未放宽。
- 缺乏可核验旧模型描述或生成前文的旧回复禁用精确重生成；未实现候选专用的模型切换选择器。
- 完整网关 evidence 的独立封存/可读导出、脱离原来源的可携带快照、原回复统一候选展示仍未完成。
- 新分支导出目前为 TXT；原 daily 的 TXT/MD 导出保持。编辑正文草稿按基础修订隔离，尚无过期草稿列表，分支标题草稿未持久化。这些交互差异不能记为计划中的完整体验已完成。
- 无分支合并、多 Agent、自主发言、世界对接、Harness、分支内换身份/模板或自动记忆作业。
- Windows 原生文件选择器取消、真机软键盘/触控/系统返回继续延期待验，不是通过。

这些限制以失败关闭保留；离线交付不等同于整个 R3b 产品发布或真实模型验收完成。

## 启动与回退

从工作树运行 `node experiments/ai-rpg-engine/character/revisions/launch.mjs C:/tmp/mm-cr3b-data/preview-final-review 43160`。当前预览已经运行，不要重复启动或共用目录。

续写页：`http://127.0.0.1:43162/revisions`。角色库端口 43160、群聊端口 43161；通过页内导航会携带可选续写入口。启动器没有真实模型选项。

回退只停止本任务启动器并撤销可选入口，保留独立数据及证据；不改共享服务或 R3a 原存档。运行核心源码变化会拒绝旧 R3b 指纹，不能重写记录哈希绕过；开发预览使用新副本/新 scope 验证。
