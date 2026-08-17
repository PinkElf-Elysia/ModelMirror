# 工作流 UI 对标 n8n / Coze / Dify 优化方案

> 状态：**批次 1–4 已实现**（2026-08-17 完成，验收通过）；批次 5 待独立立项
> 日期：2026-08-16 → 2026-08-17
> 范围：工作流（`/workflow` 经典画布及其复用的全部场景）
> 前置资料：[工作流画布UI重做方案.docx](../工作流画布UI重做方案.docx)（已完成一轮纯视觉去装饰化）
> 完成度速览：批次 1（1a/1b/2/3）✅、批次 2（4 部分/5 部分/6）◐、批次 3（7/9 ✅，8 未做）◐、批次 4 ✅、批次 5 未开始

---

## 0. 一句话结论

ModelMirror 当前是**「以 Agent(Xpert) 为中心、工作流只是画布层」**的结构；而 n8n / Coze / Dify 都是**「以工作流为中心」**的产品。对标的第一步不是改 UI，而是**确认产品定位**：是否把工作流提升为一级产品对象。本文档给出两条路线对比与成本，其余全部为可执行的画布/运行/配置/视觉改造批次。

---

## 1. 背景与目标

- 现状：`/workflow` 经典画布已完成一轮"去装饰化"（纯色背景、厚实卡片、克制分类配色），但**交互能力与产品闭环对标 n8n / Coze / Dify 仍有显著差距**。
- 目标：让工作流编辑器达到竞品级的**画布专业感 + 运行可观测性 + 配置易用性**。
- 约束（沿袭 postmortem 纪律）：有设计文档、小步可验证、不破坏稳定运行时契约（NodeContract V3 / `/api/workflow/*`）。

---

## 2. 现状盘点（代码依据）

| 模块 | 现状 | 关键文件/行号 |
|---|---|---|
| 画布 | React Flow 画布，`h-[640px]` 固定高，MiniMap 统一橙色，Controls 默认 | `WorkflowEditor.tsx:4220-4271` |
| 节点卡 | 38 种 kind 已按大类归色；紧凑竖向布局；右键显示详情浮层 | `WorkflowNodeCard.tsx:5-265,401` |
| 节点库 | 左侧面板 `hidden`，改为顶栏按钮弹出的 dropdown（`72vh/22rem`）；三 tab（工作流/中间件/知识） | `WorkflowEditor.tsx:4181-4185`、`NodePalette.tsx` |
| 节点配置 | NodeConfig 是**扁平 if 链**（~1500 行手写）；无 schema 驱动；仅 4 个表单原语；全站**无变量选择器**（`{{var}}` 全靠手输）；资源列表无缓存；布尔值走字符串 `"true"/"false"` | `WorkflowEditor.tsx:2157-3650,674-814`、`WorkflowTypedDataNodeConfig.tsx` |
| 运行 | 运行面板嵌侧栏（`embedded`），仅一个"运行"按钮，**无暂停/取消/重试/历史**；结束判定靠 `isRunning`+倒序找 `final_output`，无显式 `workflow_end` 处理；画布节点**运行中零反馈** | `WorkflowRun.tsx` |
| 后端事件 | 有 `node_start`/`node_end`/`node_delta`/`workflow_meta`/`workflow_end`/`error`；**无 `node_failed`**；`node_end.status` 硬编码 `"completed"`；节点严格顺序执行 | `server/main.py:10264,14756,10252` |
| 持久化 | 经典草稿存 **localStorage**（`modelmirror-workflow:{id}`）；后端无独立工作流 API；发布唯一路径 = 转 Xpert 草稿 | `utils/workflowStorage.ts`、`xpertApi.ts` |
| 复用面 | 同一 `WorkflowEditor` 被 **3 处复用**（Classic / XpertStudio / MetaPlanner）；改一处影响三处 | `XpertStudioPage.tsx:465`、`MetaPlannerV2.tsx:888` |

---

## 3. 竞品共性能力基线（对照表）

| # | 能力 | n8n/Coze/Dify | ModelMirror 现状（2026-08-17） | 差距 |
|---|---|---|---|---|
| 1 | 节点级运行状态（呼吸/勾/失败红） | ✅ | **已实现**：运行中青色呼吸+脉冲、成功绿 ✓、失败红 !（失败态用 `error(node_id)` 推断，无需后端改动） | 🟢 |
| 2 | 右键菜单（节点+画布） | ✅ | **已实现**：节点=配置/复制/删除/添加注释；画布=粘贴/适配视图/清空选中 | 🟢 |
| 3 | 拖拽连线生成节点 | ✅ | **已实现**：`onConnectEnd` 落空白处弹迷你选择器，选中即创建+自动连线（资源/中间件绑定端口除外） | 🟢 |
| 4 | 常驻可搜索节点面板 | ✅ | **已实现**：左侧可驻留侧栏（可收起），三 tab + 搜索 | 🟢 |
| 5 | 快捷键/复制粘贴/撤销重做 | ✅ | **部分**：右键菜单内复制/粘贴 ✅；`Cmd/Ctrl+C/V/Z/Y` 快捷键 ❌；撤销重做栈 ❌ | 🟠 |
| 6 | 运行控制台 + 日志↔节点联动 | ✅ | **部分**：日志↔节点联动 ✅（点步骤卡片画布定位）；运行控制台仍在侧栏，未底部化 | 🟠 |
| 7 | 自动整理布局 | ✅ | ❌ 未做 | 🟡 |
| 8 | 空画布/起始模板引导 | ✅ | ❌ 未做（自动造 3 节点，无引导） | 🟡 |
| 9 | 变量选择器（点选插入 `{{var}}`） | ✅ | **已实现**：全站可复用 `VariableInsertMenu`，已接入 LLM 提示词（光标处插入） | 🟢 |
| 10 | 取消/暂停/重试/运行历史 | ✅ | **已实现**：取消(AbortController)/重试(↻)/运行历史(实例内)/显式终态(workflow_end·error·cancelled) | 🟢 |
| 11 | 工作流列表/模板/导入导出/导航入口/发布闭环 | ✅ | ❌ 未做（批次 5，独立立项） | 🔴（产品层） |

---

## 4. 战略分叉：两条路线（需用户确认）

### 路线 A：工作流优先（完全对标竞品结构）
把工作流提升为一级产品对象：新增**工作流列表页**、**模板库**、**JSON 导入导出**、**导航入口**、**后端持久化 API**、**独立发布闭环**；统一三条割裂路径（经典 localStorage / native 校验台 / Xpert draft）。

- 优点：与竞品同构，产品叙事完整。
- 代价：**大型结构调整**（新页面 + 后端 API + 数据迁移），周期数周，风险高。
- 与架构现状的张力：当前后端是 Xpert 中心，工作流定义只是 `draft.workflow` 字段；独立化意味着数据模型与存储调整。

### 路线 B：Agent 优先 + 画布打磨（保持定位）
保持"以 Agent 为中心"，只做第 5/6/7 节的四层打磨（画布/运行/配置/视觉），不新增大产品对象。工作流继续作为 Xpert 的画布层，但把它打磨到专业水准。

- 优点：**全部为纯前端或极小后端改动**，风险低、可小步验收、一次惠及三处复用页面。
- 代价：不解决"工作流没有独立管理/模板/发布闭环"的产品层缺口。

### 路线 C（推荐）：两段式
**先做 B（第 5 节，立即开工），把 A 作为第二阶段独立立项**（涉及后端与产品结构调整，单独评审）。理由：
- B 的每一批都是"纯前端/极小后端、独立可验证"，符合 postmortem 纪律；
- A 的收益依赖 B 的画布体验兜底——先有专业画布，再谈工作流独立化；
- 避免一次性大改导致回归面不可控。

---

## 5. 分阶段改造计划（路线 B 的实施批次）

> 每批独立可验证，可单独合并。标 🔵=纯前端；🔴=需后端小改。

### 批次 1：画布核心体验（对标 #1/#2/#3/#4）—— ✅ 全部完成

**1a. 节点运行态（运行中/成功）🔵 ✅**
- 新增 `WorkflowRun → WorkflowCanvas` 节点状态回写：`WorkflowRun` 消费 `node_start`/`node_end`，通过 `onNodeStatusChange(nodeId, status)` 把 `data.runStatus` 写回画布 nodes。
- `WorkflowNodeCard` 读取 `data.runStatus` 渲染：运行中青色呼吸边框+脉冲点、成功绿 ✓ 角标。
- 依据：后端 `node_start`（`main.py:10264`）/`node_end`（`main.py:14756`）已就绪，纯前端可用。
- 注意：`annotation` 节点后端短路 skip（`main.py:10257`），不产生任何节点事件，静默跳过；`input`/`output` 不发 `node_delta`，依赖 `node_end` 的 output。
- **实现细节**：`runStatus` 走 `WorkflowNodeData`，保存/序列化时用 `stripRunStatus` 剥离，避免污染 localStorage 与 Xpert 存档（`WorkflowEditor.tsx`）。

**1b. 节点失败态 🔵 ✅（实现方案与计划不同：无需后端改动）**
- 计划原拟后端补 `node_failed` 事件。**实现时发现**：节点内部错误会发 `error`（带 `node_id`），前端可据此标记失败，并用 `node_end(completed)` **不覆盖**已失败节点 → 纯前端可实现。
- 顶层致命错误（无 `node_id` 的 `error`）把仍在运行的节点一键标红（维护 `runningNodesRef`）。
- 失败节点红色边框 + `!` 角标。
- **遗留**：`annotation` 节点仍无法呈现运行态（后端短路 skip）；失败节点的 `error` 事件与 `node_end` 的顺序假设依赖后端现状，若后端未来调整错误语义需复核。

**2. 右键上下文菜单 🔵 ✅**
- `onNodeContextMenu` + `onPaneContextMenu` 自制浮层菜单（不引新依赖）。
- 节点菜单：配置节点 / 复制 / 删除 / 添加注释。
- 画布菜单：粘贴（带剪贴板）/ 适配视图 / 清空选中。
- `WorkflowNodeCard` 现有右键"显示详情"改为**双击节点**触发，释放右键给画布菜单。
- **偏差**：计划的"重命名"未实现（改标题仍走配置面板）。

**3. 拖拽连线生成节点 + 节点库侧栏化 🔵 ✅**
- 拖拽连线生成：`onConnectEnd` 落在空白处 → 弹出迷你节点选择器（搜索+图标列表）→ 选中即创建并自动连线。资源/中间件绑定端口自动排除（必须手动连 workflow_agent）。
- 节点库：dropdown 改**左侧可驻留侧栏**（可收起，移动端堆叠、xl 三列）。
- **偏差**：计划的"搜索框 `/` 或 `Cmd+K` 唤起"未实现。

### 批次 2：运行体验（对标 #6/#10）—— ◐ 部分完成

**4. 快捷键 + 复制粘贴 + 撤销重做 🔵 ◐（复制粘贴已做，快捷键/撤销未做）**
- `WorkflowEditor.tsx` 已有 keydown 监听，但**仅扩展了右键菜单内的复制/粘贴**（节点序列化到剪贴板 state + 位置偏移 + 边引用重映射）。
- **未做**：`Ctrl/Cmd+C/V/Z/Y` 全局快捷键、`Space` 平移、`F` 适配视图；撤销重做快照栈。

**5. 运行控制台底部化 + 日志↔节点联动 🔵 ◐（联动已做，底部控制台未做）**
- 日志↔节点联动 ✅：运行步骤卡片改为可点击，`onStepSelect` 回传画布 `fitView` 定位+选中对应节点（`handleStepSelect`）。
- **未做**：运行控制台仍在侧栏，未迁到**底部可展开控制台**（Dify 风格）。

**6. 取消/重试按钮 + 运行历史 🔵 ✅**
- 取消：`AbortController` 中断流，取消后节点清回 idle、记录历史；后端 `/api/workflow/run` 对断开响应正常结束（已验证无副作用）。
- 重试：运行结束后出现 ↻ 按钮一键重跑。
- 运行历史：实例内 `runHistory` state，顶部"最近运行"列表（完成/取消/异常+摘要）。**未接后端 run_registry**（跨页面历史仍需 /runtime）。
- 显式终态 ✅：消费 `workflow_end`（completed）/ 顶层 `error`（error）/ 取消（cancelled），修复原"仅 `isRunning` 布尔推断、无法区分正常结束与中断"的问题。

### 批次 3：配置体验（对标 #9 及配置层缺口）—— ◐ 部分完成

**7. 变量选择器组件 🔵 ✅（对标 Dify 最大交互缺口，全站可复用）**
- 新增 `client/src/components/workflow/VariableInsertMenu.tsx`：`＋ 变量` 按钮 + 搜索下拉，从工作流节点输出变量枚举，点选**在光标处插入 `{{变量}}`**（含光标定位），带空态/禁用态。
- **已接入** LLM 节点提示词字段；**其余字段（template/code/variable_assign/agent 提示等）尚未接入**，作为可复用组件待扩散。
- 未复用 `DataTableValueBinding`（其变量为手输字符串，与本组件"枚举上游变量"目标不同）。

**8. NodeConfig schema-driven 化 🔵 ❌（未做，仍为扁平 if 链）**
- 明确**未实施**：NodeConfig 仍是从 `data.kind === "xxx"` 逐 kind 手写 if 链（~1500 行），无 schema 驱动、无统一分组/必填 `*`/tooltip。
- 新增一个 kind 仍需改 7 处。保留为独立后续项。

**9. 配置细节修复 🔵 ◐（部分完成）**
- 清理死代码 ✅：删除 `WorkflowNodeCard.outputName()`（123 行，确认无调用）。
- 错误提示语义修正 ✅：新增 `errorNotice` state，`handleConnect` 校验失败/节点创建失败改红块，成功提示仍绿。
- **未做**：默认值不自洽修正（`condition` 判断 `user_input` 但 `code` 消费 `llm_output`）；`pythonCode` 死代码；资源列表缓存（NodeConfig 挂载即 fetch 无缓存）。

### 批次 4：视觉统一 🔵 ✅（基本完成）

- 画布高度 `h-[640px]` → `calc(100vh - 15rem)` 自适应（`WorkflowEditor.tsx`）。
- MiniMap 节点色按 kind 大类映射：青=LLM/转换、琥珀=条件/迭代、teal=知识、violet=智能体、sky=工具/数据表、slate=输入输出（`minimapNodeColor`，与 `nodeMeta` 配色呼应）。
- 保存按钮提升为主行动色 brand 青（`bg-brand-300`）。
- **未做**：顶栏整体紧凑 toolbar 化（仍是 pill 按钮组）、文本 i18n。

### 批次 5（路线 A，独立立项）：工作流产品化

- 新增 `/workflows` 列表页（搜索/重命名/复制/删除）。
- 模板库：消费后端已有 `GET /api/workflow-native/templates`（当前无人用）。
- JSON 导入导出 + 分享。
- 导航入口：`theme/resources.ts` 加 workflow。
- 后端工作流持久化 API（当前仅 localStorage + Xpert draft 字段）。
- `convertToXpertDraft` 幂等化（当前重复点击重复建 Xpert，`WorkflowEditor.tsx:4014`）。
- 清理死代码：`WorkflowEditorPage.tsx`（无路由无引用）。

---

## 6. 影响面与风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 三处复用 | `WorkflowEditor` 被 Classic / XpertStudio / MetaPlanner 复用，回归同时打挂三处 | 每批改动后三处冒烟 |
| 1b 需后端改动 | 补 `node_failed` 事件触及 `main.py` 执行链 | 单独批准，小步（仅新增事件，不改现有状态流） |
| 新交互零测试保护 | 现有 7 个 workflow 测试都不渲染 `WorkflowEditor`/`WorkflowNodeCard`，右键/快捷键/变量选择器无覆盖 | 新功能配套补测试 |
| 右键冲突 | `WorkflowNodeCard` 已有 onContextMenu 详情浮层 | 并入新菜单，移除旧行为 |
| schema-driven 大改 | NodeConfig 重构动 1500 行 | 后置批次，单独排期 |
| 契约稳定性 | NodeContract V3 / `/api/workflow/*` / 节点注册表不可破坏 | 全部改动不触碰 registry payload 与运行序列化 |
| 性能 | `updateNodeData` 逐键全量 `setNodes` 无防抖 | 配合变量选择器/大文本字段加防抖（批次 3） |

---

## 7. 验证方案

每批验收（手工 + 自动）：

1. **构建**：`cd client && npm.cmd run build`（tsc + vite 全量编译通过）。
2. **单测**：`cd client && npm.cmd run test:run`，重点回归 `components/workflow/` 7 个既有测试 + 新功能补测。
3. **三处冒烟**（每批改动后）：`/workflow` 经典画布、`/agents/studio/:id` XpertStudio、MetaAgent 生成结果三处均正常渲染/运行。
4. **运行链路**（批次 1-2）：跑一个含 llm→condition→code 的草稿，观察节点运行中/成功/失败高亮、日志联动、取消重试、运行历史。
5. **配置链路**（批次 3）：新建条件节点验证默认值自洽；用变量选择器插入 `{{var}}`；资源列表首次加载后有缓存。
6. **契约回归**：`/api/workflow/node-registry` 返回 V3 payload 不被前端校验拒绝（`hasNodeContractV3`）。

---

## 8. 待用户确认项（已落地决策回顾）

- [x] **战略分叉**：已选 **路线 C（先 B 后 A）**，批次 1–4（画布打磨）已实现，批次 5（工作流产品化）独立立项。
- [x] **1b 后端改动**：**未改后端**。实现时发现 `error(node_id)` 事件足以支撑失败态，纯前端完成。
- [x] **批次范围**：批次 1–4 已全部实施（其中 4/5 部分、8/部分 9 未做，见第 5 节标注）。
- [x] **视觉主色**：工作流页主行动色已切 brand 青（保存按钮）；hire 橙保留为招聘会主题的营销色与次级强调。

### 后续待定项（供下轮排期）

- [ ] 批次 2 剩余：全局快捷键（`Cmd/Ctrl+C/V/Z/Y`、`Space`、`F`）+ 撤销重做栈。
- [ ] 批次 2 剩余：运行控制台底部化（Dify 风格，侧栏腾给节点配置）。
- [ ] 批次 3 剩余：NodeConfig schema-driven 化 + 表单原语 + 资源列表缓存 + 默认值不自洽修正。
- [ ] 批次 4 剩余：顶栏紧凑 toolbar 化。
- [ ] 批次 5（路线 A）：工作流列表页 / 模板库 / 导入导出 / 导航入口 / 后端持久化 / 发布闭环。
- [ ] 运行历史接后端 run_registry（当前仅实例内）。

### 下轮优先级建议（源自 `workflow-ui-benchmark-analysis.md` 第四节的遗留不足）

> 按影响面排序，前 5 项为建议优先项。

| 优先级 | 事项 | 性质 | 影响 |
|---|---|---|---|
| P0 | **批次 5：工作流产品化**（列表/模板/导入导出/导航/后端持久化/发布闭环） | 产品层，涉及后端 | 工作流从"Xpert 的画布层"升级为一级对象，是"一句话"目标的发布承载 |
| P0 | **NodeConfig schema-driven 化** | 中型重构（~1500 行 if 链） | 新增 kind 成本从改 7 处 → 1-2 处；同时解决松散类型/无分组必填 tooltip/无防抖/资源缓存等一批问题 |
| P1 | **后端补 `node_failed`**（或给 `node_end` 加失败 status） | 后端小改 | 失败态从"推断式降级"升级为"契约明确"，可支撑单节点失败重试 |
| P1 | **全局快捷键 + 撤销重做栈** | 纯前端 | 补齐效率工具感（批次 2 剩余） |
| P1 | **运行控制台底部化** | 纯前端 | 侧栏腾给配置，运行日志全宽（批次 2 剩余） |
| P2 | 节点默认值不自洽修正（condition/code 链路） | 需评估草稿迁移 | 消除新手建链即断的误导 |
| P2 | `annotation` 节点运行态呈现（后端需产出事件） | 后端小改 | 让注释类节点也参与可观测 |
| P2 | 顶栏紧凑 toolbar 化 + i18n | 纯前端 | 视觉/国际化打磨 |
| P2 | 性能一致性：资源列表缓存 / `updateNodeData` 防抖 / `serializeWorkflow` 抽单 / `resume` 两套机制统一 | 前端+后端 | 消隐患、防漂移 |
| P3 | 新交互补测试（右键/变量选择器/取消重试/日志联动） | 前端测试 | 现有 workflow 测试不渲染编辑器，新交互零保护 |
