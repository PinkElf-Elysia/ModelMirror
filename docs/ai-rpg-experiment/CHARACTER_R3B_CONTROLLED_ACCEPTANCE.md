# R3b 受控补齐验收（2026-11-14）

## 交付结论

真实受控传输适配、原入口共用任务预算和候选交互已实现；验证全部使用本地 mock。未调用产品模型、未读取真实凭据、未提交/推送/部署；不能将本报告解释为真实模型或账单验收。

独立预览：`http://127.0.0.1:43182/revisions`，角色库 `http://127.0.0.1:43180/characters`，群聊 `http://127.0.0.1:43181/groups`。数据为 `C:/tmp/mm-cr3b2-data/preview-final`，只有原创中性夹具。基线、接口、授权与回退见 CHARACTER_R3B_CONTROLLED_HANDOFF.md。

## 逐项验收

| 能力 | 结果及边界 |
| --- | --- |
| 受控单聊/群聊生成 | 原 transport + localhost mock 通过；V1/V2 参数合同；未接真实上游 |
| 原入口与候选共享额度 | 共享持久化 task cap + source cap + 原宿主预算；重复、并发、金额不足、缺价及已有预留通过 |
| 请求恢复 | 原响应丢失、只查询、回执幂等、不自动采用；未派发取消有持久化阻断证明；absent 不重放 |
| 真实模型选择 | 本次可切换可信清单模型；不变成员默认；原入口未核价模型/参数保存前拒绝 |
| 原文、回执与身份 | raw/SSE/receipt 与固定模型解析保留；更换 actualModel、capability 等拒绝；旧身份/版本继承不变 |
| 候选交互 | 原回复与候选按位置分组；预览不采用；恢复原回复另分支；历史候选采用截断新分支并保留旧后文 |
| 草稿与导出 | 正文/标题未保存确认、刷新保留；TXT/MD 当前有效对话；不导出未选候选或回执 |
| 页面 | 桌面与 390 CSS px 窄屏通过；原文对话框后候选保持展开；跨页导航通过 |
| 旧路径 | af3beb63/0cb019df 角色、3fffd5b4 世界、组合基线旧存档补验通过；a6563a9b 三项既有失败保留 |
| 独立预览重启 | 27 个原数据文件哈希不变，原 mock-results 文件也不变；仅新增所有权恢复记录 |
| 原证据 | 原树 74 文件、原清单 29 数据证据均无哈希变化；原报告不覆盖 |
| 设备项 | 原生文件选择器取消及真机键盘/触控/系统返回仍延期 |

## 执行证据

所有日志和本地截图位于 `C:/tmp/mm-cr3b2-data`，不入库。

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| R3b Windows 最终 | 48/48 | revisions-final.log |
| R3b 禁外网 Linux、只读源/临时数据 | 48/48 | revisions-linux-final.log |
| 角色/R1/R2/R3a/R3b 聚合 | 首次 177/183；3 项依赖链接环境失败、3 项旧世界失败。环境修正后相关补验 4/4，未冒充整套首次通过 | character-regression.log、archive-retest.log |
| 原受控传输/状态/会话/参数 | 144/144 | shared-control-regression.log |
| Windows 受控网关合同 mock | 187/187 | gateway-regression.log |
| 前端相关四文件 | 53/56，3 项旧帮助失败 | frontend-regression.log |
| 父前端构建 | 通过，保留大 chunk 提示 | client-build-final.log |
| 聊天前端构建 | 通过 | chat-build.log |
| 最终完整页面流程 | 11/11 | browser-controlled-final/results.json |
| 历史采用、标题草稿、编号定向页验 | 3/3（最终组合限制加入前，同一 UI 源） | browser-followup/result.json |
| 专用进程中断/重启 | 27/27 原文件不变，0 新派发 | restart-result.json |
| 帮助图 | 2 张新增中性图合规；全库保留 24 处旧诊断 | help-images.log |
| Git/保护清单 | 暂存为空；无原树/清单证据变化。组合树 diff --check 保留继承的 CRLF 行尾诊断 | scope-audit.json、diff-check.log |

命令：`node --test` 指定 revisions/character/conversation/group/daily 测试；原受控回归采用 `--test-concurrency=1` 指定 controlled-provider/controlled-session/controlled-state/model-control/generation-session 五文件；Python pytest 指定 test_rpg_character_contract/group_contract/authoring_route/authoring_execution/parameters_v2；Vitest 指定 helpContent/RpgLibrary/RpgAuthoringEntry/RpgChatState；两个 npm build。Linux 使用已有镜像 `modelmirror-server-node24-test:latest`、`--network none`、只读挂载与 /tmp，无拉取或部署。

## 新失败修复与既有失败归因

- V2 首次 0/2：护栏把 effectiveParameters 与参数配置混比。改按 V2 parameterProfile.parameters 匹配后 2/2；旧回执验证器不变。保留 v2-first/debug/debug2/fixed 日志。
- 缺价用例首次失败：旧通用 model 校验允许 price:null。新增金额硬上限入口提前拒绝，最终通过；不削弱校验。
- 首次页面 5/6：查看原始回执后重绘折叠候选。按候选稳定 ID 恢复展开状态后 11/11。另修正没有原回复时从候选 0 编号、标题草稿成功保存后未清理问题。
- Windows 存档复制依赖 junction 导致 3 项 EPERM。只把新工作树 engine node_modules 换成原依赖的独立副本，未改包/锁文件，相关 4/4。首次若干 helper 使用原默认测试根目录创建随机新目录，没有覆盖既存证据；后续明确传本任务测试根。
- a6563a9b 的 AUTHORING_RUNTIME_BINDING_MISMATCH / OFFLINE_SESSION_CORRUPT / JOINT_SESSION_CORRUPT 三项与上轮一致；不机械更新哈希或接受范围。
- 旧帮助语音基线、步骤数量、voice-library 图片基线三项失败和全库截图 24 处诊断保留。旧 studio legacy fingerprint 断言的主线复现结论继续引用上一轮报告，本轮未重跑该非直接依赖文件。
- 原 R3b_ACCEPTANCE.md 中广域检查中断、并发漂移、旧环境失败等证据原样保留；本轮定向成功不能抹去历史首次失败。

## 未验证与进入真实验收条件

未验证：真实路由/账单/模型表现、最新远端主线组合、共享部署、人工页面签收及设备延期项。新增独立受控入口没有替换共享运行服务。旧 v1 修订记录不自动迁移；完整旧群网关证据仍依赖原目录，不宣称独立证据包完成。

下一轮须先确认精确模型、次数和美元预算、材料及停止条件；冻结可信价格/输出上界、旧消耗和所有未知预留，并确保本任务原入口与新入口全部经过同一护栏。之后才能另行授权少量真实调用。回退仅停止本轮预览，保留数据与原证据。

## 最终范围

组合树共 90 个变更文件（继承 74、新增 16）；相对继承清单修改 12 个文件，限定在 revisions 与本篇帮助文章。新增包括 2 张中性帮助截图、受控适配/预算/组合入口/mock 测试与 4 篇任务/交接/验收/帮助记录。原单角色、世界、群聊、R1/R3a 的运行源码未被本轮再次修改。完整哈希列表在 scope-audit-final.json。暂存区为空，未提交。
