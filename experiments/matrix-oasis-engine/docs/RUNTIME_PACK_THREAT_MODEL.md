# R3 Runtime Pack 威胁模型

状态：R3.4 已实现合同、规范 JSON、Pack/Receipt Validator、确定性 Compiler、安全 CLI、独立 Runtime 执行和黑盒 parity 控制；Creator 原子锁步将在 R3.5 验证。

## 保护目标

- 冻结 Authoring 输入、参考模拟语义与历史证据不被静默改变。
- Runtime Artifact/Receipt 可确定性复现并能发现篡改、错配与非规范输入。
- 两套执行器不共享隐藏实现，诊断不泄漏输入、路径或底层异常。
- 模块保持无父依赖、无网络、可独立拆分和回退。

## 主要威胁与控制

| 威胁 | R3 控制 |
| --- | --- |
| 修改冻结 R1/R2 权威 | 固定基线、冻结路径、通用阻断码 |
| 未审批文件绕过范围 | 精确文件 allowlist，新包仅五个前缀，未知路径失败关闭 |
| Compiler 非确定或 Artifact 被改 | 单快照编译、规范 JSON、SHA-256、byteLength、20 次/并发确定性、公开 Validator 自校验与篡改负测 |
| 孤立 UTF-16 代理项被编码器替换并造成哈希碰撞 | 以确定性小写 `\uXXXX` ASCII 转义保留原代码单元，与真实 `U+FFFD` 分离 |
| Runtime 与 Authoring 语义漂移 | 独立 evaluator、禁止 R2 `src/**`、包根黑盒 parity、全部操作符/效果/目标、精确轨迹与有界可达状态探索 |
| 同 contentVersion 的 Artifact 或快照错配 | Runtime prepare 强制 Receipt；快照同时绑定 source SHA-256 与 artifact SHA-256，错配使用静态失败 |
| 一侧成功或候选状态污染当前状态 | parity 复合快照先在局部计算并完整投影比较；差异只返回静态失败，不返回候选状态 |
| 路径逃逸或外部能力注入 | 模块内 realpath、无网络/环境/持久化、Runtime 源码禁用 `node:*` |
| CLI 半成品、覆盖或清理越界 | 同父暂存、`wx+` FileHandle、句柄回读、bigint 身份、单次 rename；既有/竞态目标不覆盖，身份不明暂存或目标不递归删除 |
| 诊断泄漏 | 静态错误码与白名单字段，不回显原值、路径、异常或堆栈 |

Receipt 是完整性与复现证据，不是签名、身份或信任证明。Node 无可移植 `openat`，所以 CLI 不承诺抵御同用户恶意宿主在最后文件系统边界制造空文件或在返回后继续篡改；它只在写入 Pack 内容前绑定并复核 FileHandle 身份，对可观察替换失败关闭。R3 不处理恶意宿主、供应链签名、加密或远程分发。

Parity 是对冻结合同与当前可达测试域的差分证据，不是数学证明。两套执行器禁止共享 evaluator，降低共同实现错误自证的风险；但 Compiler、合同解释或测试投影中的共同错误仍可能同时影响两侧。R3.4 通过中性权威夹具、可替换集成夹具、完整 opcode 覆盖、精确轨迹、错误诊断和有界 BFS 缩小该风险，不宣称覆盖无限状态或未来格式。
