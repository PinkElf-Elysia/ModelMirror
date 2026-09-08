# Trust-only 审阅与首次安装边界

## 本批契约

本批是证明工具的治理前置批，不是 V0.2 产品批。P2R 保持
`disabled_no_go`、`productVisible=false`，不调用模型，不建立任何资格结论。
既有 AR1/V0.1 能力、主客户端、模型桥、Compose 和产品入口均不变。

- 开工基线：`436d24535c6f0ad80b3a4f18830fb834f90a7b95`。
- 独立分支：`codex/ai-research-trust-review`。
- 唯一目标：提供 base-owned 的 trust-only 只读审阅入口，并明确其证据权限。
- 精确五文件：本文、`scripts/trust_review.py`、
  `tests/control/test_trust_review.py`、`source-lock.json`、
  既有 `.github/workflows/ai-research.yml`。
- 不修改现有 `zero_footprint.py`、Full bootstrap 或 verify 包装器。
- 本地实现授权不包含 Commit、Push、PR、Merge、Deploy、模型调用或共享栈操作。

## 首次安装不能自证

开工基线没有 `trust_review.py`。加载器只能从事件的固定 base commit
读取审阅器；缺失时非零退出，报告
`bootstrap_required: base_reviewer_missing`。禁止回退到 HEAD、候选 artifact、
内联重写的审阅器、远程脚本或放宽原 Full。

因此首批治理候选**不能通过自己新增的工作流证明自己已经受信任**。
本地单测可以验证契约，却不是受信任 Full 或自动晋升证明。首次进入 main
需要另行授权的维护者治理审阅：固定候选 SHA/tree、精确五文件 blobs、完整
Diff 和独立测试证据；记录尚未具备 base-owned 自动审阅的事实。未获得这种
治理决定时保持未提交/未晋升，不以红灯改 warning、平台例外或跳过门禁解决。

本批仅在 `lockedFiles` 增加审阅器、测试和本文的大小/SHA-256。
不改历史 `coreBaseline.trackedFiles`，不改来源 commit、镜像、依赖或客户端
reference aggregate。它们属于不同证据层。

## 后续候选的精确路由

所有路由以固定 base/candidate 的 Git blobs 为依据，不信任工作树覆盖、PR
标题或标签。SHA 必须精确且 base 是 candidate 的祖先；base 审阅器本身必须
匹配 base blob 及 source-lock。代码不会导入或执行 candidate Python。

| 路由 | 精确变化范围 | source-lock 可变叶节点 |
| --- | --- | --- |
| `path_order` | `scripts/zero_footprint.py`、对应 `tests/control/test_zero_footprint_base.py`、source-lock | 前两文件各自 size/hash，以及唯一 aggregate；最多 5 项 |
| `diagnostics` | 上述三个文件，加 `scripts/verify.sh`、`scripts/verify.ps1` | 四个代码/测试文件各自 size/hash；最多 8 项；aggregate 不变 |
| `functional` | base module-boundary 许可且未受保护/锁定的功能文件 | source-lock 完全不变；继续原 Full |

排序和诊断不得合并；任何 T/F 混合、额外文件、增加/删除/重命名、mode 改变、
symlink/submodule、重复 JSON key、类型偷换、额外 JSON pointer、descriptor
不符均拒绝。`generatedAt` 也不属于维护路由的可变字段。
boundary、workflow、审阅器、审阅器测试和本文不允许由上述维护路由改变；
将来修改治理契约必须再次进行独立治理审阅，不能借 trust-only 通道自我放宽。
代码文件的 hash 必须变化且精确匹配 Git blob；合法等长修改不要求 size 变化。

排序实现仍在独立三文件批；诊断仍在独立五文件批。排序晋升之后，诊断必须
从新基线重新应用并重新计算锁，不能复制旧 source-lock 或旧 Full 回执。

## 两条 CI 证据通道

1. **基线侧：`pull_request_target` / `base-trust-review`。**
   仅 checkout 事件的 `base.sha`，HEAD 只 fetch 为 Git objects；权限仅
   `contents: read`，不传入 secrets，不 checkout/执行候选，不读候选测试
   artifacts/caches。先审查精确范围，再从 base 锁定的历史 source commit
   archive 字面量 `client/`，使用 base 已锁定 Dockerfile 和固定 Node digest
   在独立 hosted runner 的新目录构建。构建不接触主检出区、共享服务或数据卷。
   规范摘要由 base 审阅器重算，不调用 candidate 的排序函数。
2. **候选侧：`pull_request` / `trust-tests`。**
   Windows/Linux 各运行固定窄测试；Python 固定 3.12.13，测试依赖从 base
   的 hash-locked requirements 安装。记录 JUnit 与 base/head/tree/platform
   绑定，但这些是 `untrusted_ci_observation`，不能授权晋升或修改基线侧结果。
   候选 CI 自己的 `classify` 只控制任务路由，不是受信任的范围证明。

原 `verify` 的 trust-file 拒绝逻辑原样保留；只有 functional 路由执行它。
trust-only 时该 job 显示 skipped，scope/source-proof 回执标为
`full=not_applicable_trust_only`，不是 Full 通过。functional 的基线侧 job
只做范围审阅，不声称运行过条件跳过的 source proof。
push-main 的路由比较 `event.before` 与当前 SHA，仅是合入后的观察，不是合入前
授权；缺失/全零 before、非祖先、混合批次均失败关闭。

固定 Dockerfile 内的 `npm ci` 仍联网恢复 hash-locked 依赖。该 source proof
不是 hermetic 构建或完整供应链认证；不得将其描述为离线构建。容器不接收
候选代码、外部 cache、Provider/GitHub 凭据或事件派生 build args。

这一分离遵循 GitHub 对 [pull_request_target 的安全说明](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)：
来自候选的代码、依赖和构建脚本只能作为数据审阅，不能在受信任事件执行。

## 回执与独立人工审阅

`scope` 绑定 base/candidate commit/tree、base reviewer/workflow、精确变化路径、
JSON pointers、lock/boundary 和全部 lockedFiles 的 Git mode/blob/size/hash。
`source-proof` 再绑定逐文件规范路径、大小和 SHA-256，核对固定来源的
fileCount/bytes 与唯一 candidate aggregate。它只表示范围和 source proof
一致，仍为 `promotion=manual_required`、`qualification=not_run`。

维护者可用 `record-tests` 和离线 `review` 检查两端 JUnit、原始报告哈希、绑定、
失败/跳过和必须存在的回归用例。XML 是不可信输入，不能运行其引用的命令。
离线组合不是来源签名，也不能证明调用者提供的构建目录确实来自 CI；必须
同时核对基线侧 job 的真实事件、固定 SHA、日志和独立 source-proof artifact。
测试报告即使一致也不能代替对排序算法、取消失败路径或诊断代码的人工审阅。

输出仅写新建的证据目录，以原子独占方式发布，禁止覆盖已有文件、路径逃逸
和符号链接；回执自己的 canonical SHA-256 不含其自身字段。
不得把通过的范围审查表述为“已合并”“已晋升”或“科研资格通过”。

## 本地验收入口

使用现有固定 Python 3.12.13 环境及模块 `requirements-test.lock`；不安装产品
依赖，不运行 live qualification。Windows 的 pytest 临时根应使用新的短路径。

```powershell
# 从 extensions/ai-research 执行；<fresh-short-temp> 必须是本批的新目录。
python -I -B -m pytest tests/control/test_trust_review.py -q -p no:cacheprovider --basetemp <fresh-short-temp>
python -I -B -m pytest tests/control/test_trust_review.py tests/control/test_zero_footprint_base.py tests/control/test_trusted_full_bootstrap.py tests/control/test_boundary_base.py -q -rs -p no:cacheprovider --basetemp <another-fresh-short-temp> --junitxml runtime/diagnostics/trust-review/result.xml
git diff --check
git status --short
```

测试应主动推翻：缺 base reviewer/候选 fallback、自改 reviewer、T/F 混合、
错误 descriptor、lock 额外 pointer、类型混淆、不安全 Git mode/path、重复规范
路径、畸形/失败/空 JUnit、错误 SHA/tree/platform、receipt 覆盖，以及诊断在
规范 aggregate 晋升前试图通过。CI 静态测试必须保留原 Full 门禁，并确认
target 事件不执行候选测试/构建、不下载其 artifacts。

首次实际基线的 `scope` 应非零退出且不产生通过回执。合成 Git fixtures 的
成功路径仅是单元测试，不是对当前未提交候选的晋升。没有真实 GitHub run
时只报告“本地验证”，不可报告“CI 已通过”。

## 后续停止点与回退

维护者另行批准首次治理安装及后续排序/诊断 T 晋升后，才从最新 main 刷新
功能候选 F。保留 PR #357 Draft；不能夹带 verifier、source-lock、CI 或入口。
最终选定 T 上使用干净 detached worktree 运行一次
`trust=T, candidate=T, base=T, mode=full`；再为刷新后的 F 运行新 trusted Full
和实际 GitHub 检查。旧回执不能沿用，任何 P0/P1 或未解释冲突仍阻止交付。

本地回退只需放弃这个未提交的独立候选，不删除任何现有证据/数据。
若未来已合入，须经另行授权的窄 revert 恢复治理五文件；原 Full 的拒绝逻辑
始终不变。恢复后排序/诊断晋升重新被阻止，不能靠删除门禁开放产品能力。
