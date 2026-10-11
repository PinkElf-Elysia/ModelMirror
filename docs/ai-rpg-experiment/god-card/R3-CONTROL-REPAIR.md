# R3 控制面修复与用户额度更正

2026-10-11。用户明确要求先修复控制面阻断，并将上次控制面失败豁免，恢复已用 2/4、剩余 2 次。此更正只适用于该次请求，不建立自动退款规则。

## 网络根因与修复范围

运行中的 `modelmirror-server` 将 `openrouter.ai` 解析为 `198.18.0.60`，非公网地址，符合出口策略拒绝条件。容器使用 Docker DNS `127.0.0.11`，其上游为宿主解析。没有进一步修改或审计 VPN 的全局配置。

通过 HTTPS 查询 Google DNS 得到公网 A 记录 `104.18.2.115`、`104.18.3.115`，TTL 300 秒。在保留原 `/etc/hosts` 内容后，仅增加带 `rpg-god-r3-public-dns-repair` 标记的两行解析；备份保存在容器 `/tmp/rpg-god-r3-hosts-before.txt`。

这是一项当前容器内的定点修复，**不是上游 Fake-IP 的永久治理**。容器重建将丢失覆盖；公网 IP 也可能变化，之后再次调用前应重新验证，不能永久依赖本次解析。没有重建共享容器，没有修改模型连接、密钥、资格、白名单或出口防护。公网域名仍使用正确 Host/SNI 和 TLS 证书校验。

修复后使用现有 `ProviderEgressPolicy` 通过地址授权；无凭据 HEAD `https://openrouter.ai/api/v1/models` 返回 200。原 Fake-IP、元数据地址和环回地址仍被拒绝。以上为网络/出口验证，不是生成质量或付费 Provider 测试。

## 可追溯额度豁免

- 原授权 `rpg-god-b4-20261010` 仍为总限额 4。
- 新增服务端授权修订 `god-r3-user-exemption-20261011`，只豁免原 `slot-3`。
- 豁免绑定原请求 ID、失败 result.json 的 SHA256 `8b0bf6fd46aabbe29c7b37af7af45118efacddd3e369a78f5360e920d1aee66d`，以及授权 ID。缺失、重复、成功记录、跨卡或内容不匹配均拒绝。
- 不删除、重命名或复用原 slot-3；下一次可用物理槽位为 slot-4、slot-5。配额仍最多允许 4 次计费尝试，而非将业务限额改为 5。
- 当前实际 API 返回 `limit=4, used=2, remaining=2, reserved=0, attempts=3, exempted=1`。
- 原请求 claim、未知操作和失败证据保持不变。豁免不等于原操作恢复成功，也不授权重放。

授权修订在忽略目录 `experiments/ai-rpg-engine/.rpg04-work/god-card/b4-real/R3-BUDGET-AMENDMENT.json`；原 AUTHORIZATION.json 未覆盖。仅可信启动入口加载，前端不能提交豁免。

## 验证与兼容

- `node --test experiments/ai-rpg-engine/studio/budget-exemption.test.mjs experiments/ai-rpg-engine/studio/memory-tasks.test.mjs`：33 通过。
- `node --test experiments/ai-rpg-engine/god-card/tests/real-host.test.mjs experiments/ai-rpg-engine/god-card/tests/tasks.test.mjs experiments/ai-rpg-engine/studio/controlled-provider.test.mjs`：21 通过。
- 启动脚本语法检查通过，18512 实际 API 验证额度 2/4。全部回归为可控假 Provider；本次真实生成次数为 0。
- 已核对归属，仅重启 God 自有 18511/18512 预览；18514 原展示验收入口保留。R2 不重测、不重冻。
- 预算宿主代码变化产生新运行版本，原未知会话仍可读，但为 `RUNTIME_VERSION_MISMATCH`，不静默升级。后续生成须使用兼容新会话；本轮未创建或发送新的剧情。
- 仍未证明 God 记忆提取泛化、下一轮召回或分支真实内容承接。

实际状态和改动源码 hash 记录在忽略目录 `.rpg04-work/god-card/r3-repair/verification.json`。原 `R3-RESULT.json` 与 `R3-PLUGIN-REAL.md` 保留失败发生时的事实；当前状态以 STATUS.json 及本修订为准。

## 回退

网络回退仅移除带本批标记的 hosts 行；若 hosts 没有其他后续修改，也可恢复容器内备份。不得覆盖其他任务后续增加的行。此操作恢复原 DNS 路径，也会恢复未解决的 Fake-IP 阻断。

额度回退须停本轮自有预览并保留全部数据和授权修订；关闭豁免将恢复原计数，但不得删除失败槽位。旧代码只在数据副本验证，不能对含新计数状态的数据目录直接降级写入。

没有 Commit / Push / PR，没有共享栈重建或部署；共享服务唯一运行时变更是上述单域名 hosts 修复。自动回执错误原因丢失的历史问题未在本次改写，原 unknown 不伪装成已收束。
