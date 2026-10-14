# R8-A 后 Provider 出口清单漂移收口

## 范围与证据边界

本批独立处理出口登记漂移，不实施 R8-B，不修改运行逻辑、扫描器、CI 工作流、权限开关或 Managed 资格。不调用 Provider、不读取凭据、不操作共享栈。清单登记不是外发授权，也不是真实模型验收。

- 最近清单更新：`171af984`。
- R8-A 合入前主线：`6ff70de608b7ef7972a4ed023f38ce7f44110635`。
- R8-A PR #415 头提交：`0fb371ff6d2b1c5830977b3cf051d42f589ec178`。
- 本批审计基线：`2df7efbaa1e91bfc625cf6e9bb5a10e0162ab0cc`。
- [PR #415 Backend quality 失败日志](https://github.com/PinkElf-Elysia/ModelMirror/actions/runs/38416373668/job/115361776713)：失败于独立步骤 `python -B scripts/check_provider_coverage.py`，其后的后端测试等步骤未执行。不能用先前全量 pytest 通过替代该门禁，也不能描述为 CI 全绿。

三个固定 Git archive 均在断网、只读根文件系统、无额外 capability 的临时容器中解包并建立临时 Git 索引，使用未修改的扫描器复现。只导入扫描器，不导入应用；校验器仍从源码 AST 读取 workload 注册表。

| 固定快照 | 登记入口 | 扫描调用点 | 未登记 | 过期登记 | 漂移模块 | 门禁错误 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 最近清单更新 `171af984` | 80 | 935 | 0 | 0 | 0 | 0 |
| 上游 `6ff70de6` | 80 | 944 | 19 | 10 | 26 | 30 |
| 合并后 `2df7efba` | 80 | 944 | 25 | 16 | 32 | 42 |

结论：上游已有 10 组调用点替换、9 个新脚本登记缺口；R8-A 另引入 6 组替换。32 个模块中，24 个仅上游变更、6 个仅 R8-A 变更、2 个两者均有变更。未登记调用点不等同于新增真实 Provider 出口。

环境必须对齐：CI 使用 Python 3.12，本次使用缓存镜像中的 Python 3.12.14。主机 Python 3.13.9 的 AST 表示差异会额外改变指纹，不能据此刷新清单。本批不修改指纹算法或提高 CI Python 版本。

## 逐项核对

以下路径以仓库根目录为基准。每项均核对对应版本差异及当前调用边界；仅在确认语义后更新登记。

### 仅上游变更

| 路径 | 审阅结论与登记边界 |
| --- | --- |
| `client/src/components/SpeechWorkspace.tsx` | Unicode 字符计数及受限语速输入；既有后端 API，不持有 Provider 派发权。 |
| `client/src/components/settings/NewApiChatCertification.tsx` | qualification 状态投影；原认证 API 未变化。 |
| `client/src/components/settings/ProviderWorkloadControlSettings.tsx` | qualification 摘要及过期状态展示；原控制 API 未变化。 |
| `client/src/data/agents.ts` | 静态目录与 Prompt 文本命中扫描词；无模型派发实现。 |
| `client/src/data/models.ts` | 静态模型目录；不是 Provider transport。 |
| `client/src/pages/RpgPage.tsx` | God 卡导航、同源 status 与 iframe；实际模型出口在服务端。 |
| `client/src/pages/RpgPluginsPage.tsx` | 插件兼容卡与文案；插件安装不是推理，但不能宣称无副作用。 |
| `experiments/ai-rpg-engine/card-replica/src/session-client.ts` | earth/god 作用域的同源会话 RPC；send 可间接触发推理，浏览器不持有 Provider 凭据。 |
| `experiments/ai-rpg-engine/god-card/lib/plugin-host.mjs` | 注入的 summary/memory fetcher 使用合成响应；generate 回调仍是委托边界，登记 `model_delegate/manual_harness`。 |
| `experiments/ai-rpg-engine/god-card/lib/plugin-runtime.mjs` | 离线 profile 与文件哈希校验；HTTP 模块名称命中扫描，不是实际外发。 |
| `experiments/ai-rpg-engine/god-card/lib/ui.mjs` | 本地资源响应及 sec-fetch 请求头校验；没有模型派发。 |
| `experiments/ai-rpg-engine/god-card/preview.mjs` | loopback 静态 GET/HEAD；Provider 禁用、CSP 禁止连接。 |
| `experiments/ai-rpg-engine/god-card/tools/integration-preview.mjs` | 固定 loopback 代理；Provider 禁用、模型限额零；不认证真实调用。 |
| `experiments/ai-rpg-engine/god-card/tools/presentation-preview.mjs` | 隔离会话副本、GET-only 代理；禁用 Provider 与写操作。 |
| `experiments/ai-rpg-engine/god-card/tools/ui-preview.mjs` | 固定 loopback UI 代理；Provider 禁用、模型限额零。 |
| `experiments/ai-rpg-engine/god-card/tools/real-first.mjs` | 显式授权后可派发 session send 并产生费用；登记 `model_delegate/rpg_bridge`，保留不确定结果、不自动重发。 |
| `experiments/ai-rpg-engine/god-card/tools/real-preview.mjs` | 授权文件约束的人工预览，使用服务端 token 调用受限 bridge；登记 `model_delegate/rpg_bridge`，不能误记为离线 HTTP。 |
| `experiments/ai-rpg-engine/studio/controlled-provider.mjs` | earth/god 模型/profile 白名单、loopback 控制面、参数校验与终态回执；维持 `model_delegate/rpg_bridge`。 |
| `experiments/ai-rpg-engine/studio/server.mjs` | 入站 HTTP 与可选 God host；默认离线，但配置的 delegate 能推理；维持入口分类，不把整个服务认定为无副作用。 |
| `server/model_router/chat_certification.py` | qualification 历史及过期状态摘要；实际调用点未变。 |
| `server/model_router/rpg_bridge.py` | 固定 God 模型/profile、目录范围及派发前拒绝回执；仍使用已授权流式 transport。 |
| `server/multimodal/audio_catalog.py` | 手工音频能力 profile；目录声明不能替代实际资格验证。 |
| `server/multimodal/stt.py` | 手工转录 profile；既有外发调用点未变。 |
| `server/multimodal/tts.py` | 支持的声音/语速参数及边界校验；既有外发调用点未变。 |

### 仅 R8-A 变更

| 路径 | 审阅结论与登记边界 |
| --- | --- |
| `client/src/components/runtime/ClientToolPanel.tsx` | 重试按钮受服务端 retryable 限制；没有新增模型 transport。 |
| `client/src/components/workflow/WorkflowRun.tsx` | 等待/取消状态和权威取消响应投影；后端 API 边界不变。 |
| `server/client_extension/service-worker.js` | operation 与 dispatch 身份、回执及 artifact 请求头；仍是 `external_domain/external_tools`，并非模型推理。 |
| `server/office_addin/taskpane.js` | capability 检查及 operation/dispatch 缓存区分；Office 效果仍属 `external_tools`。 |
| `server/model_router/workflow_gateway.py` | 根预算 context、稳定恢复身份、dispatch ownership guard；原授权 transport 不变。 |
| `server/xpert_runtime/sandbox_toolset.py` | 打包路径排序修复；既有外部工具 HTTP 调用点未变。 |

### 两侧均有变更

| 路径 | 上游与 R8-A 的区分 |
| --- | --- |
| `server/main.py` | 上游 Decision 模型声明调整；R8-A 的 External Xpert 与 Handoff 两处 `_run_workflow_response` 增加根预算委托、执行身份与所有权保护。等待不能当作完成。维持 `model_delegate/workflow_shared`，不将所有 legacy 调用提升为 Managed。 |
| `server/model_router/workload_control.py` | 上游资格历史/准入变化；R8-A 增加 replay guard references 及原子旧 run 校验。注册表的 flags、shapes、integration status 均保持不变。 |

## 最小修复与防回归

清单只更新上述 32 个模块的指纹、16 组已有调用点身份与理由，新增 9 个逐项分类的 God 脚本登记；`baseline_sha` 指向本次实际审阅的合并提交。80 个入口定义逐字段不变，37 个 workload 注册事实不变。现存 16 组替换保留原 kind、entries、owner；其他调用点完全不变。

新增三项离线测试：

1. 9 个 God 脚本精确登记及委托/离线边界分类。
2. 浏览器扩展和 Office 回调仍归外部工具，不伪装为模型推理。
3. 将已审阅 UI 预览的 loopback 目标改为外部域名，仍同时触发未审阅调用点、过期登记及模块漂移，证明没有放宽扫描器。

修复前运行 23 项测试：新增登记检查共 11 个 subtest 失败，原 20 项及新增目标变异检查通过。此反例在更新清单前保留；没有用禁用检查获得绿测。

## 验证与回退

权威验证环境：`modelmirror-r9a1-linux-validation:4932e3b4`，Python 3.12.14；容器无网络、无端口、无凭据，仅独立证据输出目录可写。命令：

```text
python -B scripts/check_provider_coverage.py
python -B -m unittest discover -s server/tests -p test_provider_coverage.py -v
git diff --check
```

最终本地结果：原清单门禁退出码 0，80 个入口、944 个调用点全部对应；23 项离线测试通过。再次装入旧清单可复现 11 个失败 subtest，恢复已审阅清单后全部通过。测试源码语法编译、`git diff --check` 通过；本批三个变更文件的私钥/OpenRouter/GitHub 凭据模式扫描无匹配。

验证程序另断言：80 个入口定义逐字段不变；37 个 workload 注册事实不变；扫描器、CI 工作流及 workload 源码与基线一致；只有已审阅的 32 个模块、25 个新身份和 16 个旧身份发生变化，未涉及的调用点及其相对顺序不变。最终清单文件 SHA-256 为 `2707ae208a5fef3c1baf9f970cfaba2c74d072a2d65e0f11f998ff77c51a6a0e`。

远端 Backend quality 必须由后续获授权的提交重新触发，不能以本地通过改写原失败记录。此次不重复运行应用全量或前端生产构建：本批未修改运行代码、依赖或前端产物；局部门禁不代表 R8-B 已获验收。

原始复现与完整扫描结果保留在本地独立目录 `C:\tmp\modelmirror-provider-coverage-evidence-20261013`，不包含运行 Store、凭据或模型数据。固定快照归档 SHA-256：

| 快照 | SHA-256 |
| --- | --- |
| reviewed | `c812e48c248e54e7f207fde5521342f04fabd6003874ae219358ce80d17e4027` |
| upstream | `40f69b77f99e30edb412b3c3fe13a502fc2c437ee522b3f6fd6b7709ac107888` |
| merged | `018669f081b139cd1e57cc047952843b56e64544438b182c8f76bf19734d923a` |

Help Center Impact: None。本批不改变产品操作、运行恢复或配置语义，不新增截图与用户入口。

回退：恢复本批清单及配套测试/审计文档即可；这会重新暴露原漂移门禁，不改变 Provider 行为或持久化数据。后续源码变化必须重新审阅，禁止自动刷新哈希。R8-B 在门禁收口前继续暂停。
