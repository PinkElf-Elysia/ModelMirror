# 运行经验沉淀与 Creator 晋级验收记录

## 基线与隔离环境

- 验证日期：`2026-08-27`。
- 主线基线：`origin/main@bf486f25`（已包含 PR #317）。
- 使用独立 PR 工作树、独立前后端预览端口和隔离 Store 验收，未复用共享栈运行目录。
- 后端代码只读挂载；运行、Creator、Skill 与 MCP Store 全部指向临时容器 `/tmp`，未读取或重建共享栈。
- 无 Key 手工路径与真实 Provider 路径分别验证。真实调用仅发送用户确认的合成脱敏摘要；最终输出片段始终未选中，未发送 Key、真实用户数据、工具参数、附件或运行 trace。

### 最新主线复核

- `2026-08-27` 先同步并核对 `origin/main@f0150fb5`，完成完整前端串行回归；提交前再次快进到 `origin/main@62b21188`。
- 最后 4 个上游提交仅与本轮 Help 索引/测试有路径交叉；一次内容冲突已手工合并，同时保留上游 RAG V3 多样性基线与本轮 Skill Experience 条目。Experience、Creator 和运行 UI 源码无上游交叉。
- 独立后端按最新主线代码重启后，原晋级 Session 仍恢复为非空 Creator：Step 1 保留可信来源、创建结论与完整 brief，Step 2 保留两项已确认脱敏素材。
- 在最新主线重新检查 `1440`、`1024`、`390` 三档，页面宽度仍分别为 `1430 < 1440`、`1014 < 1024`、`380 < 390`。
- 下方截图是首次真实流程基线 `bf486f25` 的原始 PNG；最新主线复核没有伪造或重标截图版本。

## 无 Key 用户路径

使用一条合成、成功完成的经典 Workflow：检查发布包文件命名与扩展名，并输出可复用步骤和不应误报的边界。

1. 服务端创建唯一经验候选，页面在 `/skills/create` 的“待处理运行经验”恢复它。
2. 页面默认选中目标摘要与输入输出结构；“最终输出片段”保持未选中。
3. 确认素材后，页面明确提示未配置模型，并生成可编辑手工提纲。
4. 补全两条正例、两条近似反例、输出合同、成功标准、复用步骤、失败边界和过拟合风险。
5. 保存后“确认并打开 Creator”门禁开启；确认后进入 `/skills/create/{session_id}?step=2`。
6. Creator 不是空白会话：Step 1 可见可信 Workflow 来源、创建结论、预填需求、正反例和预期结果；Step 2 保留已确认的两项脱敏素材。
7. 刷新后仍从服务端恢复同一 Session 和 Step 2，不重复创建候选或 Creator Session。

结果：完整无 Key 路径通过。

## 真实 Provider 用户路径

- 授权后，独立后端临时接入现有 newAPI 兼容网关，实际模型为 `deepseek/deepseek-chat`。只把独立验收容器接入现有网关网络；没有重启、重建或写入共享 Server/Client 与共享业务 Store。
- 网关确认共收到 `10` 次 HTTP 200：`7` 次产品分析尝试和 `3` 次只输出字段集合、枚举与列表数量的结构诊断。首次网络未接通的请求未到达 Provider，不计入该数字。
- 新建：真实模型生成完整 `create` brief，用户确认后创建非空 Creator Session，直接进入第 2 步，保留 3 项脱敏素材。
- 更新：真实模型完成分析，服务端重叠检索只开放可编辑的 Workspace Creator Skill。用户选择更新后，新草稿逐字克隆已安装版本，保持稳定 Skill ID并绑定原 version/content digest；修复后打开时停留在第 2 步资源规划，不再因已有草稿误跳第 4 步。
- 不沉淀：一次性计算被真实模型判为 `no_skill / one_off_task`。用户确认后候选进入 `dismissed`，没有创建 Creator Session。
- 三条最终产品链路的 analysis attempt 均为 `executor_mode=model`、`error_code=null`。更新目标与最终安装仍受后续资源、触发和行为质量门约束；本次没有自动生成资源、评测或安装。

## 证伪发现与处理

- 首次预览把代码目录只读挂载，但遗漏 `SKILL_INSTALLED_DIR` 与 `SKILL_TMP_DIR` 的临时重定向，重叠检索因无法创建安装元数据目录而返回 `skill_experience_store_unavailable`。
- 异常栈确认是独立预览配置问题，不是 PR4 数据或晋级逻辑缺陷。补齐容器 `/tmp` 目录后，同一路径成功完成。
- UI 原错误只写“运行经验暂时无法读取”，无法区分已安装 Skill 存储故障；现改为“运行经验或已安装 Skill 暂时无法读取。请恢复服务端存储后重试。”
- 新增前端回归，真实覆盖无 Key 的 `brief PATCH → decision → promote`，防止只验证表单显示而遗漏保存与晋级。
- 证伪发现无 Key 提纲未补全时，“这次不沉淀”也被保存门禁误禁用；改为直接调用 dismiss，不要求先保存一个本就不会晋级的 brief，并补回归。
- 证伪发现同一组件切换到另一条运行来源时可能暂留上一来源的候选状态；现在按完整可信来源重建状态边界，并验证新来源不会复用旧 Session。
- 全量串行测试曾有一条“未保存修改”异步断言撞到 Testing Library 默认 1 秒边界；单测可复现为通过，最小调整仅把该断言预算明确为 2.5 秒，未修改产品行为。
- 提交前文件头审计发现截图扩展名为 `.png`、实际编码却是 JPEG；已原位转换为真实 PNG，并重新核对 PNG signature、尺寸和 Help 内容测试。
- 真实 Provider 首次返回 HTTP 200 后仍降级为手工提纲。根因一是 Agent prompt 只列字段名，没有冻结精确版本和值结构；现改为明确的完整 JSON 合同，严格解析器未放宽。
- 更新晋级后后端已正确返回 `?step=2`，但工作台按“已有草稿”自动恢复到第 4 步，跳过资源规划。恢复逻辑现只对“运行经验更新且尚无 Resource Plan/Build”的会话固定回到第 2 步；其他 Creator 恢复规则不变，并补真实预览与前端回归。
- `no_skill` 真实响应稳定给出服务端固定原因，但合理地省略可复用步骤、正反例或自由文本说明；旧完整性规则反而要求模型为一次性任务编造可复用内容。现在 `no_skill` 以固定原因枚举完成判断，同时 create/update 覆盖在决策层与 promotion 最终门仍必须具备完整可复用 brief。
- 稀疏 `no_skill` 卡片原先同时显示“提纲完整”“请补全目标”“待补充”，语义冲突。现改为“判断完成 + 固定原因 + 唯一不沉淀动作”；若用户反向选择新建，才展开完整提纲且未补齐时按钮保持禁用。

## 视口与截图

- `1440 × 900`：来源卡可见，页面 `scrollWidth=1430 < innerWidth=1440`。
- `1024 × 768`：来源卡可见，页面 `scrollWidth=1014 < innerWidth=1024`。
- `390 × 844`：待处理素材卡与 Creator 来源卡均为单栏，页面 `scrollWidth=380 < innerWidth=390`。
- 用户可见截图：`client/public/help-center/bf486f25/creator-run-experience-prefilled.png`，`990 × 891`、`353,597` 字节，文件头为标准 PNG signature。
- 截图使用合成需求，不含地址栏、Key、Token、真实用户数据、内部路径或完整运行输出。

## 验证结果

- 经验 Foundation、Distillation、Promotion 后端：`66 passed`。
- Middleware handoff、Creator Session、Workflow run contract、Xpert Chat 回归：`58 passed`，仅保留既有 FastAPI `on_event` 弃用告警。
- 运行经验、Creator 页面与帮助内容定向前端：`4 files / 44 tests` 通过。
- 并行完整前端首轮为 `9 failed / 754 passed`：8 项为固定 5–10 秒超时，1 项为同轮额外请求计数；7 个失败文件分组或单文件隔离重跑全部通过。
- 完整前端随后以单 Worker 重跑：`123 files / 763 tests` 全部通过（`720.78s`），确认首轮为并发资源噪声而非功能回归。
- 快进 `origin/main@62b21188` 后，7 个后端相关/交叉文件联合重跑：`124 passed`，仅保留既有 FastAPI `on_event` 弃用告警。
- 最新主线上的前端定向测试：`4 files / 44 tests`；typecheck：通过。
- 最新主线上的前端生产构建：通过（`3163 modules transformed`），保留既有大 chunk 告警。
- Help 页面实际加载截图：`naturalWidth=990`、`naturalHeight=891`，`1000px` 视口无横向溢出。

## 回退

设置 `SKILL_EXPERIENCE_PROMOTION_ENABLED=false` 可恢复旧的 Creator capture 入口；候选 Store 数据保留。回退不删除 Creator Session、不修改已安装 Skill，也不影响 Workflow 或 Xpert 的原始运行结果。
