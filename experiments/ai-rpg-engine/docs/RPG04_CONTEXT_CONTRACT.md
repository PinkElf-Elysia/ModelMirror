# RPG-04 上下文辅助合同

## 04A2a：结构层交付

本批只定义四种独立 `0.1.0` 格式及 `validateContextStructure(kind, value)`。Schema 与格式常量深冻结。所有对象闭合，未知字段拒绝；通过 JSON preflight 后才校验，不执行 getter、不修改输入，失败诊断不回显正文、未知字段名、绝对路径或堆栈。核心无文件、网络或模型调用。

| 格式 | 内容 | 信任边界 |
|---|---|---|
| context-profile | profile 身份、卡包及受信模板引用/hash、预算、场景、显式别名、世界书匹配声明 | 模板只有引用；卡片数据不能提供系统提示词或运行地址 |
| context-input | 原卡包/玩家/会话、profile、本轮显式 input.kind、场景及资源引用、生成标识和受控调用参数 | 本地完整输入，不是可公开回执；不执行选择或生成 |
| prepared-turn | 资源/profile/template/session/revision 绑定、原运行请求及装配回执 | 结构成功不代表已验证消息或可派发 |
| context-receipt | 绑定/hash、计量方式、可公开来源引用、可见世界书选择及历史投影摘要 | 不允许正文/title/keywords/自由命中理由；offline/mock/real 是声明，不能自行证明真实调用 |

匹配声明支持场景和资源 ID、关键词 all/any/not、声明状态 eq/ne、优先级与显式冲突组/替换引用。它们只是数据；本批不实现匹配器。多条兼容命中可共存，可选条目未命中不是错误。预算保留 input/lore/history/output 上限；结构层不把字节估算等同精确 token。

## 后继 04A2b 必须完成

- `validateContextProfile(value, cardPackage)` 校验版本、类型化资源引用、场景/开场/世界作用域、稳定 ID、别名歧义、状态条件类型和替换关系。所有资源使用冻结的旧卡包字段，不能增加别名字段到资源对象。
- 输入语义校验消费冻结运行校验器；核对原卡包/玩家/会话、当前 revision、pending/active 状态及命令引用。纯层通过注入 hash 校验 canonical JSON，不引入 I/O。
- `validatePreparedTurn(value, bindings)` 核对当前会话与资源/profile/template hash、request/receipt 相同生成 ID、revision、输入和消息 hash。严格拒绝漂移；结构校验不能替代绑定校验。
- `validateContextReceipt(value)` 核对计量方法与 accuracy、计数/限额、稳定引用与重复项等内在约束。公开选择回执的条目必须在后继绑定校验确认 player/shared，不能泄露 host 元数据。真实调用证据仍由运行回执和账本核对。
- 四个公开业务接口中 `compileContext` 留 04C；不以占位函数伪装可执行编排。语义校验与编译均不得自行提交状态。

## 实测与文件范围

命令：`node --test tests/context-schema.test.mjs tests/contracts.test.mjs tests/rpg04-boundary.test.mjs`：47/47（新增结构 10、旧合同 28、边界 9）；`node scripts/check-boundary-rpg04.mjs` 返回 `RPG04_BOUNDARY_OK mode=complete frozen=baseline-blobs`；`git diff --check` 无输出。

本批五文件：`context/schemas.mjs`、`context/index.mjs`、`tests/context-schema.test.mjs`、`docs/RPG04_CONTEXT_CONTRACT.md`、`docs/RPG04_STATUS.json`。原合同、fixture 和业务测试未改。新增测试明确证明结构校验允许语法正确但未绑定的引用，以防后继误把它作为派发许可。

04A2 尚未全部完成，下一子批为 04A2b。网站成功请求 0/30，Provider 派发 0/8，未提交/发布。独立新 Astra 审查与整轮人工验收尚未执行。

## 04A2b：语义绑定交付

本批新增 `context/contracts.mjs` 并从 `/context` 导出：

- `validateContextProfile(profile, cardPackage)`：资源版本、场景/开场/世界、别名类型/作用域/歧义、世界书引用、声明状态条件类型、替换引用与循环。该接口不接收 hash 服务，卡包字节 hash 由输入验证补查。
- `validateContextInput(input, {hash})`：复用旧卡包、玩家和会话校验，核对资源 hash、revision、pending/active、场景、命令与输出预算。hash 必须是受信同步函数，输入为 canonical JSON 字符串，输出小写 SHA-256。
- `validateContextReceipt(receipt)`：校验计量方法、总数/限额及重复项；不单凭此接口断言来源、真实派发或叙事质量。
- `validatePreparedTurn(prepared, {input, hash, hostTemplate})`：hostTemplate 是调用方受信登记中的 `{id, version, sha256}`，不是由卡片自动授权的模板。核对资源/profile/template/session/revision、request/receipt 绑定，重算消息与生成输入 hash，校验可见世界书及来源引用、最近已提交历史后缀。绑定服务不是认证签名，不证明提示词内容安全；消息构建、可信模板正文及可见性投影由后继 04C 实现，04D 才衔接运行。

所有接口返回稳定只读报告，不修改输入、不提交状态、不派发。host 必需条目被阻断，host 条目不能进入绑定后的公开选择回执。当前不承诺从任意文本中检测秘密或注入，也不把声明的 real 分类当成真实 Provider 证据。

本批五文件：`context/contracts.mjs`、`context/index.mjs`、`tests/context-contracts.test.mjs`、本说明、`docs/RPG04_STATUS.json`。实测：语义 13、结构 10、旧合同 28、边界 9 共 60/60；完整边界 `RPG04_BOUNDARY_OK mode=complete frozen=baseline-blobs`；差异检查通过。替换关系先全量校验引用再遍历，覆盖后置悬空引用及循环反例。

04A2 已完成结构与绑定小批；`compileContext`、选择/预算算法、真实网站参考与模型验收未实施。下一批 04B1。网站 0/30、Provider 0/8，未提交/发布，整轮仍 in_progress、claimAllowed=false。
