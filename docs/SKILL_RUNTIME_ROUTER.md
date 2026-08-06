# 私有 Agent Skill 按需路由

最后更新日期：2026-08-06

## 适用范围

Skill Router 只服务于私有 `workflow_agent` 运行，包括 Xpert 对话、经典/原生工作流、Goal 与 Handoff。公共 Xpert App 仍禁止使用 `skills_runtime`；Agent Workspace、Skill 上传、skill-creator、外部市场和语义重排不在本轮范围内。

目录发现与安装默认关闭。旧工作流继续使用既有 `skill_ids` 和 `auto_discover` 行为，不会因为升级而获得额外 Skill 权限。

## 配置

`skills_runtime` 增加三个字段：

- `catalog_search: false`：允许检索本地已核验目录和已安装 Skill。
- `catalog_install: false`：允许通过人工审批安装或升级目录 Skill。
- `max_catalog_installs: 3`：单次 Agent 运行成功新增或升级的硬上限，取值 1–3。

启用 `catalog_install` 时必须同时启用 `catalog_search`，并给同一 Agent 绑定覆盖 `skill_install` 或 `*` 的 `human_in_the_loop`。工作流静态校验和运行时编译都会重复检查这条约束。

经典画布中开启“允许经人工审批安装目录 Skill”时，会自动启用目录检索，并自动添加或复用同一 Agent 上的人机审批中间件，将 `skill_install` 加入需审批工具。若 Skill 中间件尚未绑定 Agent，画布会先创建已配置的人机审批节点，并明确提示将两个中间件通过紫色端口绑定到同一个 `workflow_agent`。关闭目录安装不会自动删除审批节点或移除已有审批工具，避免破坏用户的其他安全规则。

经典画布加载时会用当前服务端中间件注册表刷新已有节点的字段定义，并为新增字段补入默认值；已有 Skill ID、自发现开关和未知扩展配置均保留。因此升级前创建的 `skills_runtime` 节点无需删除重建。

## 工具调用顺序

Agent 只在现有能力不足时调用 `skill_find`，不会预注入候选，也不会访问网络：

1. `skill_find({ need, limit? })`：从服务端本地索引和已安装库返回最多 6 个结果、匹配理由、候选指纹及 `active | installed | stale | missing` 状态。
2. `skill_enable({ candidate_id, candidate_fingerprint })`：激活已安装且指纹一致的 Skill，仅对当前运行生效。
3. `skill_install({ candidate_id, candidate_fingerprint })`：对目录中的 `missing` 或 `stale` 候选创建审批；批准后按固定 SHA 全局安装或升级，并仅授权当前运行使用。
4. 激活后必须先调用 `skill_read`；只有需要脚本、模板或其他资源时才调用 `skill_stage`。

模型不能提交任意仓库、子目录或 SHA。服务端会按候选 ID 和指纹重新解析安装源；目录或已安装元数据发生变化时返回 `skill_candidate_stale`，要求重新检索。

## 审批、安全与恢复

`skill_install` 是非只读、不可并行且必须审批的工具。审批卡片的数据由服务端可信元数据生成，显示 Skill 名称、来源仓库、子目录、当前 SHA、目标 SHA，以及“全局安装、仅本轮授权”。该审批只允许批准或拒绝，不能编辑安装参数。

审批暂停状态保存当前运行的 `active_skill_ids`、成功安装计数和已拒候选。恢复时继续原工具调用，不重跑已经完成的模型决策或工具步骤。拒绝后，同一候选在本轮不能再次申请；失败安装不计入上限，也不会激活。

安装替换使用同目录暂存、旧版本备份和原子元数据写入。目录替换或元数据提交失败时恢复旧目录和旧 `source_ref`，避免升级失败破坏已安装版本。

## 索引与可观测性

服务端索引由顶层 Skill、SkillSet 成员注册表和成员搜索元数据原子生成，只收录具有固定 SHA 的 `ready` 安装源。候选记录稳定 ID、候选指纹、目录指纹、分类、标签、所属集合、固定安装源和预规范化检索字段；索引缺失、指纹不一致或覆盖不完整时检索失败关闭。

运行时检索会动态合并已安装但不在目录中的本地、插件和草稿 Skill。这些候选只能激活，不能由 Router 重新安装。

检查点记录候选 ID、结果数量、查询哈希、来源 SHA、安装动作和当前安装计数，不记录完整用户需求。前端通过 `skill_runtime_status` 显示检索、激活、安装、升级和拒绝状态。

## 验证

```bash
python -m pytest server/tests/test_skill_finder.py server/tests/test_skill_runtime_router.py -q
python -m pytest server/tests/test_skill_integration.py server/tests/test_workflow_agent_task_node.py -q
python -m pytest server/tests/test_xpert_runtime_sandbox.py server/tests/test_xpert_runtime_approvals.py -q
python -m pytest server/tests/test_workflow_native_validate.py -q
cd client
npm.cmd test -- --run src/components/runtime/RuntimeApprovalPanel.test.tsx
npm.cmd run build
```

出现问题时可分别回退服务端索引提交、运行时授权提交和前端体验提交；旧工作流的静态 Skill 行为不依赖 Router。
