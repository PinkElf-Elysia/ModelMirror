# R8-B B0：离线反例与准入证据

## 开工契约

- 基线：`origin/main@ec79e1203c0f26e9efa20e4e75e7e0bf9de2a795`，PR #416 已合并，前置远端 Quality 检查通过。
- 工作树：`C:\tmp\modelmirror-meta-planner-r8-b-readonly-subflows`。
- 分支：`codex/meta-planner-r8-b-readonly-subflows`；开工时工作树干净。
- 目标：证伪旧子流程实现能否直接满足 R8-B，而不是本批开放子流程能力。
- 允许修改：本任务卡、`server/tests/test_workflow_r8b_entry_evidence.py`。
- 禁止修改：生产代码、NodeContract/Planner 开放范围、持久化格式、依赖、部署、Provider 清单和其他工作树。
- API、用户界面与数据迁移：无变化；Capability 保持 V10/22 类。
- 风险：测试涉及临时文件和离线子进程；不读取凭据，不外发请求，不执行业务写入。
- 模拟结果不能证明真实模型表现；Store 快照重建不能替代后续完整进程崩溃验收。

## 验收口径

`required` 测试表达待交付的 R8-B 约束，失败必须保留，不使用 skip/xfail 或放宽断言。
`existing` 和 `legacy` 测试是已有行为对照，其通过不代表该行为满足 R8-B。
尤其不能把旧 classic 子流程允许网络调用或嵌套调用误称为 R8-A 引入的回归。

1. 同进程根预算与已完成结果复用作为对照。
2. 子执行必须有持久父预算绑定，空 ContextVars 恢复不能绕过已耗尽根预算。
3. 同一发生标识的单次输入改变必须拒绝，不能返回旧输入的结果。
4. 从真实模拟派发时快照恢复，结果未知不能重复派发。
5. 批量目标、输入摘要、数量、根身份漂移必须拒绝；子项身份稳定且彼此独立。
6. 旧发布/激活入口及 kind 级策略不能充当传递性只读证明；只静态构造合成 POST 目标，不执行它。

## 验证命令与结果

环境：已缓存 Python 3.12 离线验证镜像 `modelmirror-r9a1-linux-validation:4932e3b4`，
镜像 ID `sha256:8cd2dcf996cfd7c0d2bdf261d5d9358df528343ed9d625f0d101e768e202e8a7`。
测试容器断网、只读挂载源码；运行副本及所有 Store 仅在容器临时目录。

```text
python -m pytest server/tests/test_workflow_r8b_entry_evidence.py -q -ra -p no:cacheprovider
python -m pytest server/tests/test_workflow_subworkflows.py server/tests/test_workflow_iteration_v2.py server/tests/test_workflow_r8a_inline_budget.py -q -ra -p no:cacheprovider
```

结果：

| 检查 | 状态 | 实际结果 |
| --- | --- | --- |
| B0 首次反例 | 失败 | 4 failed / 7 passed / 4 warnings，21.59s |
| 旧子流程、迭代 V2、R8-A 内联预算对照 | 通过 | 52 passed / 4 warnings，12.36s |
| 新容器重复 B0 反例 | 失败 | 相同四项失败，4 failed / 7 passed / 4 warnings，16.53s |
| Python 3.12 显式 UTF-8 AST 语法检查 | 通过 | 断网镜像读取新增测试，无语法错误 |
| 新增两文件 whitespace 检查、受限敏感模式扫描 | 通过 | 无 whitespace 诊断，无密钥模式或 skip/xfail 命中；不是完整秘密检测证明 |
| 已跟踪 Diff / 最终状态 | 通过 | 已跟踪文件无变化；仅任务卡和测试两个新增文件 |
| 全量后端、前端构建、独立预览 | 未运行 | 本批仅固定离线反例，尚未修改生产实现 |
| 真实 Provider | 未运行 | 无本批调用授权；测试边界使用合成结果 |

四条 warnings 均为现有 FastAPI `on_event` 弃用提示。对照命令退出 0，反例命令退出 1；
这不是全绿门禁，不能用于提交功能 PR 或开放 Planner 能力。
宿主 PowerShell 管道语法检查曾因中文编码产生 `UnicodeEncodeError`；
改在相同 Python 3.12 镜像中直接按 UTF-8 读取后通过，未改源码绕过。
普通沙箱的部分 `git diff` 出现工作树读取错误，提升只读权限后确认已跟踪 Diff 为空。

实际隔离启动方式如下；对照与复跑使用同一隔离方式、分别执行上面的 pytest 命令：

```powershell
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,size=2g --mount type=bind,source=C:\tmp\modelmirror-meta-planner-r8-b-readonly-subflows,target=/source,readonly --entrypoint python modelmirror-r9a1-linux-validation:4932e3b4 -B -c "import os,shutil,subprocess,sys; shutil.copytree('/source/server','/tmp/repo/server',ignore=shutil.ignore_patterns('__pycache__','storage','.env','.env.*')); os.chdir('/tmp/repo'); os.environ['PYTHONDONTWRITEBYTECODE']='1'; os.environ['AGENT_TASK_STORAGE_DIR']='/tmp/r8b-runtime'; sys.exit(subprocess.call([sys.executable,'-B','-m','pytest','server/tests/test_workflow_r8b_entry_evidence.py','-q','-ra','-p','no:cacheprovider']))"
```

## 已证实的反例与因果链

| 反例 | 观察结果 | 生产代码定位（本基线） |
| --- | --- | --- |
| `test_required_subworkflow_has_durable_root_budget_link` | 子执行已完成，但 `execution_budget_parent` 为 `None` | `server/main.py:27465` 的部署运行桥没有传入 `runtime_budget_delegation`；`:11367` 对普通 workflow 不建立通用执行预算；`:11567` 的持久绑定分支因而未触发 |
| `test_required_restart_cannot_bypass_exhausted_root_budget` | 第一子调用消耗根预算 1/1；重新加载 Store、使用空 ContextVars 后，第二子调用仍派发，合计 2 次 | 同进程预算仅由 `use_execution_budget` 继承；缺少持久父链后，新后台上下文无法恢复根限制 |
| `test_required_single_call_input_drift_rejected_after_restart` | 同一调用节点换了已解析输入，未抛冲突，仍复用旧结果 | `server/workflow_deployments.py:2293` 仅批量调用携带摘要；单次发生标识只含父执行和调用节点，复用校验未包含已解析输入 |
| `test_required_dispatched_unknown_snapshot_is_not_replayed` | 恢复派发后、结果前的实际快照，原调用标识再次派发，合计 2 次 | `server/main.py:6801` 只协调已终结的 durable 状态；过期 running 且未终结的子执行继续进入 `execute_workflow_trigger`，没有在该调用边界证明外部请求未派发 |

这些是当前旧入口与 R8-B 目标约束之间的已复现差距，不声称全部由 R8-A 引入。
第一、第二条是同一预算缺口的结构与行为证据，不应算成两个独立架构根因。
第四条通过恢复真实模拟派发时的两个 Store 文件来重建故障点，不是实际杀死进程；
仍需后续进程级、迟到执行者及跨 Store 写入顺序故障注入。

## 已有能力与只读资格边界

- 已证实：同进程根预算计量、已完成子结果重启复用通过。
- 已证实：批量输入摘要、固定版本、项数和根身份漂移均拒绝，失败前后 Store 字节不变。
- 已证实：批量子项标识跨 Store 重载稳定，两个索引产生不同 task/execution ID，越界拒绝。
- 已证实：旧 callable 发布/激活接受包含合成 POST 的目标及两层包装调用；测试仅发布到临时 Store，未执行 HTTP。
- 已证实：当前 `http_request` kind 级效果仍为 `external_read`，因此不能用该标签证明包含 POST 的目标只读。
- 建议：R8-B 应建立版本化、配置级、传递性准入，不直接改变旧 classic 入口语义；未知效果、HTTP、嵌套调用/迭代和等待按本轮范围拒绝。
- 未验证：所有工具/中间件/外部 Xpert 的传递效果、完整崩溃恢复、Evaluator 隔离 Backend 传播、真实模型表现。不得据此宣布 R8-B 完成。

## 对后续批次的约束

1. B1 固定显式授权目标及版本，统一配置级传递只读判定；保留旧入口兼容，不能只换一个 kind 白名单。
2. B2 将单次调用的发生标识、已解析输入摘要、根预算授权与消费、派发及结算状态形成持久链路；结果不确定时先阻断，不能靠 lease 超时重发。
3. B3 复用现有批量预留与项身份，增加未完成项恢复与根预算集成证据，不另造第二套批量账本。
4. 在四个 required 反例及更完整故障注入通过前，不进入 Planner 开放或用模拟结果声明真实验收。

## 收口与回退

本批不提交、不创建 PR；保留反例后才能确定 B1 的最小实现边界。
仅新增测试和任务卡，无生产行为需要回退，无活动资源指针或共享栈被修改。
后续完整后端、前端构建、独立预览及真实模型验收属于实施批门禁，本批不冒充完成。
