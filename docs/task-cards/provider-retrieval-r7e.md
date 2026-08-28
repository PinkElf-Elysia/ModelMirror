# 任务卡：R7E OpenRouter Batch 收尾

## 状态与授权边界

- 记录日期：2026-08-27。
- 工作树：`C:\tmp\modelmirror-provider-retrieval-r7e`。
- 分支：`codex/provider-retrieval-batch-r7e`。
- 实施基线：`4bfef53c4b32f3fa8044122553c7a8f42bd08908`（已合并 R7D）。
- 本次收尾不新增付费调用，不执行 Commit、Push、PR、Merge 或生产部署。
- 现有主工作树、其他任务的容器和持久化数据保持不动；R8/R9 不在本轮范围。

## 实现与最小修复

- 将既有 OpenRouter Chat/Embedding Batch 接入独立 Managed Policy、精确资格、
  单 IP 安全出口、幂等本地任务映射和脱敏 Receipt；复用已有 v17，不新增 Schema 迁移。
- 同一幂等键最多一次 POST；不确定提交不重放，后续状态通过本地任务 ID 只读查询。
- 此次收尾修复前端两处查询恢复问题：首次恢复已存任务失败后会再次查询；进行中的
  轮询遇到瞬时错误后继续查询，成功时清除旧错误。组件卸载时中止请求并清理计时器。
- 补充三个组件用例：待确认提交复用请求标识、进行中查询失败后恢复、首次恢复失败后恢复。
- 待确认提示及帮助文案明确：防重复 POST 保证只属于 Managed 服务端，不属于 legacy 直连。
- 预览前端明确设置 `API_TARGET` 指向本轮后端，避免默认 `server` 名称指向共享服务。
  仅替换无数据挂载的前端容器；原后端、Router 数据和已有 Batch 均保留。

## 验证证据

### 实施基线加本轮增量

| 检查 | 实际结果 |
| --- | --- |
| `npm.cmd run test:run` | 120 个文件，707 项通过 |
| `npm.cmd run typecheck` | 通过；首次缓存写入 EPERM 经权限修正后原命令通过 |
| `npm.cmd run build` | 通过，仅保留大 Chunk 警告 |
| Batch Workspace 与 Workload Settings 组件测试 | 14 项通过 |
| 最后提示文案修改后的 Batch Workspace 测试 | 3 项通过，随后客户端镜像生产构建通过 |
| Batch、Managed Batch、Workload 后端专项 | 53 项通过 |
| 后端全量 `server/tests/` | 4839 通过、29 跳过、3 失败；失败归因和原样补测见下文 |
| Core、独立 newAPI、可选 Overlay Compose | `config --quiet` 通过；Overlay 使用显式测试 URL |
| 最终 Diff、敏感信息及文件范围扫描 | 通过；没有密钥、数据库、构建产物或日志进入变更集 |

后端专项命令：

```text
python -m pytest server/tests/test_openrouter_batch.py server/tests/test_managed_openrouter_batch.py server/tests/test_provider_workload_control.py -q -p no:cacheprovider
```

全量命令：`python -m pytest server/tests/ -q -p no:cacheprovider`，在隔离 Linux 测试容器内
运行，源码只读挂载，测试存储使用临时目录，不注入 Provider 凭据。

全量中的三项失败均为 Skill 的 Python/TypeScript 对照测试：测试加载器要求 Worker
目录中的 TypeScript，而生产镜像已移除开发依赖。仅更换 Node 版本不能解决；补齐锁定的
TypeScript 5.9.2 测试运行时后，原三项未经修改的测试全部通过。它们是：

- `test_python_and_typescript_matcher_keep_the_same_golden_order`
- `test_python_and_typescript_keep_the_same_market_recall_order`
- `test_generated_search_index_is_reproducible`

因此结论为“全量 4839 通过，加环境修正后原三项补测通过”，不是“一次全量全绿”。
早先只读源码挂载遮蔽 Worker 构建产物的测试环境问题也已隔离处理，Worker bridge 原文件
复测 28 项通过；没有削弱测试、类型检查或安全校验。

### 最新主线交叉验证

- 已 Fetch 到 `origin/main@821067a7db4811a3f3f1fd649e4fdfade9eafb22`，比实施基线领先 34 个提交。
- 在独立临时工作树 `C:\tmp\modelmirror-r7e-latest-check-20260827` 应用完整 R7E Diff，
  没有文本冲突；交叉文件为 `server/main.py`、`docker-compose.yml` 与帮助内容索引。
- 最新主线上同一组后端专项 53 项通过；前端 Batch、Settings 与帮助中心四文件专项
  40 项通过，typecheck 与修改后的 production build 通过。
- 尚未在实际 R7E 分支 rebase，也未把原基线全量结果冒充最新主线全量结果。
- 本机安装按原锁文件执行；包管理器报告的 5 项审计告警未通过无关升级处理。

## 真实验收与保留数据

- 使用此前已获授权并已完成的 OpenRouter Chat Batch，不创建第二个任务。
- 本次从修正后的前端代理只读查询原本地任务：`completed`，总计 1、成功 1、失败 0；
  usage 为 10 输入、5 输出、合计 15 Token。Provider 报告费用为 0.000004 USD，
  `billing_authoritative=false`；不是 ModelMirror 账单。
- 先前收尾核对的提交记录为一次请求；控制面表未保存 Prompt、模型输出或凭据。
- Embedding Batch 的独立形态和单 POST 由自动化测试覆盖；本记录不宣称完成新的真实
  Embedding Batch 端到端验收。
- 15150 原预览保留供用户继续检查；本轮只读恢复查询不意味着获得重新认证或重跑授权。

## Help Center Impact

- 影响用户体验：是。涉及 Batch 提交前说明、待确认提交、刷新失败恢复和本地任务编号。
- 正式文章：`client/src/content/help-center/articles/check-availability-cost-data.md`；
  索引增加 Batch 检索词。
- 最新主线的前端独立预览与帮助增量证据归档到
  `docs/help-center/evidence/provider-retrieval-r7e.md`。Docker 地址池耗尽阻断了新网络与
  最新主线后端启动，因此没有把静态前端重放扩大表述为完整后端预览。
- 帮助重放只覆盖发送前可见操作，不用新付费任务制造演示，不把自动化故障测试写成
  浏览器真实故障验收。

## 兼容性、回退与剩余边界

- 不更改普通 Chat/SSE、模型数量、提示词选择器、多模态、R5/R6 或 R7B-D 的产品合同。
- 默认 Feature Flag 继续关闭；合并不等于生产启用。
- 非终态任务先停止新增提交，保留只读查询。回退代码时保留 v17 表、任务映射、Receipt、
  Provider 凭据及所有旧数据；部署前仍需 SQLite Backup API 一致性备份。
- 服务端启动执行一轮 GET-only 恢复；页面打开后持续查询。未声称关掉页面后存在持续运行的
  服务端轮询守护进程。任务映射持久化，不依赖浏览器保存上游 ID。
- 真实上游耗时、结果保留期和最终结算不由 ModelMirror 保证；页面恢复可能需要有效的
  原 Provider 连接。任何再次提交或认证均需新的额度授权。
- 后续发布前仍需刷新主线、按授权同步实际分支，并对新增交叉变化重新验证。当前尚未发布。
