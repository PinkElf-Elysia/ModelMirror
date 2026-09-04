# Meta Planner 类型化控制流帮助与预览证据

## 基线与隔离范围

- 验收日期：`2026-09-03`。
- 分支：`codex/meta-planner-control-flow-07`。
- 基线：`origin/main@efa63af292aa7822ed0f414574e9d4e28c6403ce` 加本分支改动。
- 独立预览前端：`15408`；独立后端：`15407`。
- 后端容器：`modelmirror-control-flow-07-preview-api`；数据使用本轮独立卷，不读取或修改共享栈数据。
- 本轮预览在用户明确授权后完成一次真实候选生成；没有启动候选评测、批准 Proposal 或发布 Agent。

## 功能实操

使用生产 Graph IR V3 编译路径准备一个不调用模型的合成候选，Proposal ID 为
`proposal_43749a9871254e50bb74faa0ccd5ab00`。

1. Meta Planner 页面显示 Capability Snapshot `evoagentx-meta-planner-capabilities-v7`，可见 Planner 能力严格为 16 类。
2. 候选控制流报告显示 `1 路由 · 3 场景 · 2 成功来源`：
   - `router:case_1` 到达成功来源 `approved`。
   - `router:case_2` 到达成功来源 `rejected`。
   - `router:default` 到达错误终点 `stop`。
3. 将候选说明改为“通过语义 outcome 在互斥成功与错误终点间安全路由。”，选择“预览元数据变更”。预览只包含一个 `set_xpert_metadata` 操作，预览前 Proposal 未变化。
4. 选择“确认应用”后，Proposal 从 `r1` 精确增加到 `r2`，页面与 API 均保留新说明；没有产生 `r3`、运行或发布副作用。
5. 从“评测候选”进入 Agent 评测，创建“控制流路径验收集”，保存一个成功路径和一个预期错误路径用例。数据集成为 `revision 2`，发布按钮因编辑后校准状态为 `stale` 而保持禁用。
6. 没有绕过校准门禁，也没有把静态控制流报告当作真实模型回答质量证据。

## 真实模型生成

- 模型：Planner 与默认 Agent 均固定为 `deepseek/deepseek-v4-flash-0731`。
- 合成目标：生成批准、拒绝两条互斥成功路径，并让无法识别的请求以 `UNSUPPORTED_REQUEST` 主动终止；未发送仓库源码、用户数据、凭据或运行日志。
- RunRegistry：`dd769373-ee4f-4983-920e-55563c226e67`，状态 `completed`，无错误；从创建到完成约 `34.05s`。
- Proposal：`proposal_91321771d038435f9e26246a95d7c04e`，类型 `xpert_create`，状态 `pending`，revision `1`，未批准也未发布。
- 生成结果：Graph IR V3 状态 `current`；Workflow 与发布预检均通过，warnings 为空；Capability Snapshot 为 `evoagentx-meta-planner-capabilities-v7`。
- 候选结构：`input` 1 个、`workflow_agent` 3 个、`multi_route` 1 个、`terminate_error` 1 个、`output` 1 个，共 7 个节点、7 条边。
- 静态证据：1 个路由、3 个场景、2 个成功来源；`case_1` 到批准 Agent，`case_2` 到拒绝 Agent，`default` 到错误终点，且无不可达节点。
- 唯一修复：首轮 Blueprint 触发一次 `graph_patch_v1` 定向修复，修复后验证通过。服务固定路径为任务规划、Blueprint、一次修复，因此本次成功生成执行了 3 次 completion 调用；该计数来自受测服务路径与 `repair_used=true`，不是 Provider 账单回执。
- 预览器最初两次提交分别在“未配置网关”和容器 DNS 阶段失败，均发生在模型请求前且未产生 Proposal；随后仅将独立预览容器接入专用 Provider 网络，未重建或修改共享服务。

## 帮助中心重放

- 新增文章：`/help/review-meta-planner-branches`。
- 文章明确区分静态场景、Patch 预览、路径用例和真实模型评测，不把未执行环节描述为已验收。
- 在帮助目录搜索“路径评测”，该文章排在首位。
- 清空搜索状态后从文章地址重新打开，标题、目录、6 个操作步骤、费用提示、限制和下一步均可见并与独立预览界面一致。
- 入口与状态没有歧义，因此本轮没有添加截图资产。

## 自动验证

- 前端标准全量：`npm.cmd run test:run`，`131` 个测试文件、`916 passed`；Node 安全响应头测试 `1 passed`。
- 前端生产构建：`npm.cmd run build`，通过；仅有仓库既有的大 chunk 提醒。
- 帮助截图资产门禁：`14` 篇已注册文章、`0` 篇未注册草稿，引用、格式、尺寸、体积、替代文本和基线归属全部通过。
- 后端语法：对本轮所有修改的 Python 生产模块运行 `python -m py_compile`，通过。
- 后端证据完整性专项：`27 passed`。
- Meta Planner、Graph IR、NodeContract、Authoring、Evaluator 和 Runtime 组合回归：`211 passed`。
- 最终后端全量：隔离一次性容器执行 `python -m pytest server/tests/ -q`，`5682 passed, 29 skipped, 6 warnings`，耗时 `1802.82s`；警告均为既有弃用或前向引用警告。
- 首次误用不含 `pytest` 的桌面捆绑 Python，命令在收集前以 `No module named pytest` 退出；随后使用现有本地测试镜像原样完成上述全量测试，没有用窄测试替代。
- `git diff --check HEAD`：通过。

## 已验证边界

- Planner 可以表达并验证 Condition、Multi Route、Data Merge 和 Terminate Error 的语义 outcome，不接受模型伪造原生 Handle、route ID、join 或变量名。
- 静态分析要求每个场景恰好到达一个成功来源或错误终点，并限制路由与场景预算。
- Output V2 只接受实际到达且带可信语义轨迹的唯一来源；零个或多个来源失败关闭。
- Headless Patch 预览无副作用，Apply 受 revision 和 checksum 保护且只增加一次 Proposal revision。
- `workflow_path_match` 使用 Planner ref 与语义 outcome；旧手工工作流缺少 Planner ref 时返回 unsupported warning，不猜测物理节点 ID。
- 旧 Output V1、旧 Proposal 和旧工作流继续保留兼容读取与运行路径。

## 未验证与回退

- 已执行一次真实 Planner 候选生成；尚未执行候选回答、路径评测运行、Proposal 批准或 Agent 发布。
- 循环、等待、HITL、Handoff、Trigger、问题分类器和表达式引擎仍未向 Planner 开放。
- 回退时先关闭四类控制流 Planner Adapter 和路径指标入口；保留 Output V2 的读取与执行兼容，避免已保存草稿失效。
