# G1：终态持久化与验收证据收口

日期：2026-09-02。状态：**这是重新冻结前的历史记录，不是最终候选状态或 Full 回执。**

## 重新冻结说明（2026-09-03）

- 本文主体保留 G1 修复时的原始基线、验证和停止点，不能解释为当前分支仍未提交，也不能替代新的干净 Full 证据。
- 新集成基线固定为 `origin/main@e927db557f71db420e07a49818c0d4ae1e0d6ce3`。可信 T 是包含本说明、`source-lock.json`、`module-boundary.json` 与 `scripts/trusted_full_bootstrap.py` 的提交；最终 F 只能在其后修改 `postTrustAllowedFiles` 明列的六个 server 文件。
- Full 必须从该 T 的独立干净 detached worktree 执行可信 bootstrap。bootstrap 在运行候选 verifier 前后核对 T/候选 HEAD、全部 `lockedFiles`、文件类型、工作树清洁度和 T 后路径集合，并在候选 manifest 通过后另行生成不可覆盖的 `trusted-full-bootstrap.json`。
- bootstrap 必须用 `python -I -B scripts/trusted_full_bootstrap.py` 启动，且 `--base` 必须解析为同一 T commit；直接运行候选 `verify.ps1` / `verify.sh` 不能作为可信 Full 证明。
- 精确 T/F commit、tree、来源锁 hash 与候选 manifest hash 以当次 `trusted-full-bootstrap.json` 为准，避免在被哈希锁定的文档中制造自引用提交 ID。
- P2R Host 对阶段终止原因再次独立校验：coherence 首轮只接受 `tool_calls`，finalize 只接受 `stop`；`length`、`content_filter`、空值和未知值均不能被覆盖或记为阶段成功。
- 独立只读审阅发现并促成上述 bootstrap 修复；在新 T/F 字节形成后仍须重新核对。P2R 继续 NO-GO，本说明不授权模型调用或产品激活。

## 范围与基线

- 工作树：`C:\tmp\modelmirror-ai-research-v0-2-p2r-g1-closeout`。
- 分支：`codex/ai-research-v0-2-p2r-g1-closeout`。
- 继承 r4 HEAD：`5891ec207cb7af683313597a6b88aada06f087de`。
- 本次只读核对的本地 `origin/main`：`0ad5aa9f7e849e1874999f0a25471d331285b3f3`；本次恢复未重新 fetch，不表示远端当前状态。
- 本批属于 V0.2-P2R 前置收口，不增加用户可操作的科研阶段，不激活 Phase 3/4，不改变 `0.3.0-v0.1`、`scientificClaim=none` 或 P2R NO-GO。
- ResearchStudio、AI-Researcher、Inspect、LDR 的选型、版本与复用边界不变。规范性产品路线仍为 `AI_RESEARCH_V0_ROADMAP.md`。
- 未修改来源锁、模块边界、主客户端、根 Compose、主服务注册、数据库 schema 或 CI；未调用模型、OpenAlex、Zotero，未变更共享栈。

## 实现与用户影响

### 科研资格终态 outbox（3 个父仓文件）

`server/model_router/repository.py`、`chat_stable.py` 与对应 `test_provider_chat_stable_service.py`：

- 只有 `gateway=ai_research_scoped` 的已派发资格调用，才将无正文终态先落入 Provider 控制面自己的 `chat-completion-outbox/`，再以原子事务完成 attempt、run 与资格 gate 更新。扩展不读取此目录。
- outbox 只保存调用标识、终态、受限错误/原因、模型标识、计时、token 数和完整性 hash；不保存消息、模型输出、工具参数、凭据或 Provider URL。
- 同一终态重复写入保留首次 `stagedAt`；hash 覆盖该时间戳。同一 attempt 的矛盾终态拒绝覆盖。
- 数据库暂不可写时保留 outbox；仓库重载先对账，再处理没有终态证据的重启遗留调用。后续 scoped 资格调用前也会对账，未解决时返回 `provider_chat_completion_reconciliation_pending`，不派发新的 scoped 调用。
- 对账只写本地控制账本，不重发 Provider 请求；默认 `/api/chat` 不创建、不应用也不等待科研 outbox，并继续保留原有直接写入及 `uncertain/server_restarted` 语义。
- outbox 损坏、单字节篡改及断链符号链接会失败关闭。读取/写入均限制为 64 KiB。
- 例行 receipt 清理保留 `run.hard_failure` 与 attempt-only `result_class=hard_failure`，不丢弃历史部分落盘的失败事实。

这不是任意 HTTP 请求的 exactly-once 承诺，不覆盖整盘不可写、文件系统丢失或断电持久性，也不是针对可重写文件和 hash 的主机管理员的防篡改签名。未落入 outbox 的遗留运行仍保留原有 `uncertain/server_restarted` 语义。

### 验收证据（5 个模块文件）

`scripts/verify.ps1`、`verify.sh`、`zero_footprint.py`、新增 `acceptance_manifest.py` 及 `tests/control/test_zero_footprint_base.py`：

- 每次验证使用独立 diagnostics 子目录，不复用上次运行状态；成功回执只创建一次，不能覆盖旧回执。
- Full 开始及回执生成时校验干净工作树、固定 HEAD/tree；不允许把未提交代码的结果归到旧 HEAD。
- 记录 fixture ID、runtime audit、安全攻击结果、零增量证据、来源锁 hash、镜像标识/大小，以及三个 SBOM inventory 的 hash。状态和审计运行 ID 必须匹配，缺失或跨目录证据拒绝。
- 回执包含结构化状态；inventory 文件按 hash 引用，归档时必须连同整个 diagnostics 目录保留。回执不是独立于可信 verifier 的执行签名。
- 可选文献状态必须包含有效项目/集合标识和完整的八项成果 hash；未执行时明确 `liveLiteratureAcceptance=not_run`。`p2rQualification` 固定 `not_run`。
- 默认 Compose 比较完整服务/卷名称集合，而非仅比较数量；客户端三份独立构建证明接口不变。
- 只有本次进入启动阶段才进行模块栈清理；Quick 不再停止一个已存在的模块栈。Full 仍必须使用专用 Compose 项目、端口与网段，不能指向用户验收栈。

## 已执行验证

最终测试在固定本地镜像 `sha256:03518a43f2f1a5aa47f1d47b583924b12a00539467dc604fa86e0fa26cb68608` 中执行：`--network none`、2 CPU、4 GiB 内存，源码只读挂载并复制到容器临时文件系统；没有 Docker socket、共享数据卷或外部模型访问。该测试镜像不是本批重新构建的产品镜像。

```text
python -m pytest
  extensions/ai-research/tests/control/test_zero_footprint_base.py
  extensions/ai-research/tests/control/test_boundary_base.py
  server/tests/test_ai_research_bridge.py
  server/tests/test_provider_chat_auto_service.py
  server/tests/test_provider_chat_canary_api.py
  server/tests/test_provider_chat_canary_chat.py
  server/tests/test_provider_chat_canary_repository.py
  server/tests/test_provider_chat_certification_api.py
  server/tests/test_provider_chat_certification_repository.py
  server/tests/test_provider_chat_certification.py
  server/tests/test_provider_chat_contract.py
  server/tests/test_provider_chat_control_api.py
  server/tests/test_provider_chat_control_repository.py
  server/tests/test_provider_chat_control_service.py
  server/tests/test_provider_chat_stable_chat.py
  server/tests/test_provider_chat_stable_service.py
  -q -rs -p no:cacheprovider --basetemp /tmp/g1-final
```

以上文件参数合为一个命令。结果：**344 passed, 2 skipped, 4 warnings**。

- 两项 skip 是 Linux 容器不具备 PowerShell；四项 warning 是既有 FastAPI `on_event` 弃用提示。
- `bash -n`、Windows PowerShell AST 解析、改动 Python 的内存编译、高置信源文件 secret scan、`git diff --check`：通过。
- 实际只读 Compose 渲染：16 个服务与 22 个卷，名称集合与当前来源锁一致。这不是共享运行栈拓扑证明，也不代替客户端构建 hash。
- Windows Full 入口反证：进程级执行许可下，以 `-Base HEAD -Mode Full -DistributionMode ExternalPull` 调用，立即以 `Full verification requires a clean worktree` 拒绝；未进入镜像构建或栈启动。首次调用被系统脚本执行策略拒绝，未改系统策略。
- 实际 `validate_boundary.py --base HEAD --distribution-mode external-pull`：**失败**，首个错误 `locked file drifted: scripts/verify.ps1`。四个现有锁定文件均有预期变更：`verify.ps1`、`verify.sh`、`zero_footprint.py`、`test_zero_footprint_base.py`。这是待治理的真实门禁，不是通过结果。

反证过程保留以下失败结论：新增重复落盘测试先因错误模块引用失败，修正为 `server.model_router.repository` 后原测试通过；断链符号链接攻击首次证明 `exists()` 跳过待对账记录，修正存在性判断后原攻击及完整影响面回归通过。之前查出的“普通活跃调用被误拦”与“attempt-only hard failure 被清理”也在最终回归中持续覆盖。

## 尚未完成与下一停止点

1. **独立审阅与重新冻结**：四个受保护 verifier 文件和新增 manifest 必须与父仓运行修复分开审阅。来源锁与保护清单应由独立维护者基于审定字节更新；不得让本批自行修改 trust 文件获得通过。
2. **保持 P/T/F 分层**：可复用资产、可信验证/锁治理、父仓运行修复保持独立。新增 manifest 也应进入可信锁定/保护面；冻结后的 F 只承载批准的父仓运行差异，不能混入 verifier 或 trust 变更。
3. **干净候选 Full**：获得相应本地提交/冻结授权后，刷新并审计基线交集，再在干净候选上运行统一 Full，生成本轮 manifest、镜像/SBOM、三份客户端构建证明和真实 fixture 重启证据。本批未执行这些完整运行门禁。
4. **兼容与现场验收**：补齐 Windows 行为测试及原错误提示下的恢复操作验证。没有本轮浏览器旅程、真实 Provider、文献或新 P2R 资格证据，不得引用旧预览替代。
5. G1 不关闭 P2R NO-GO；完整 connector、verified V0.1 输入、真实 Host/coherence 与后续 ResearchStudio 主干资格仍按锁定路线另批执行。

未执行 Commit、Push、PR、Merge、Deploy 或 Publish。不得以本文件替代独立审阅或 Full 回执。

## 恢复与回退

- 正常恢复由仓库初始化或下次受管调用触发本地对账，不需要重新发起模型调用。长期 pending 应先检查本地存储/权限与原始终态事实，不能删除 outbox、改 hash 或手工改为成功。
- 回退代码前先停止新的受管调用并核对 pending；不得把有未对账终态的账本直接交给不认识 outbox 的旧代码。保留账本和 outbox，由维护者完成有证据的对账或离线恢复。
- 本批无数据库迁移。代码回退不删除任何用户数据、命名卷、outbox 或验收证据；任何数据删除仍需单独授权。
