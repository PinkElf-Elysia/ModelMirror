# RPG-03 最小会话持久化

03D1 已实现并验证；本说明属于 03D2 文档小批。存储只维持会话连续性，不构成长期记忆、检索或分布式队列。

## 接口

`/runtime/node` 导出 `openFileSessionStore({rootDirectory})`、`sha256(text)` 和纯函数 `recoverSession(session, cardPackage, playerSetup, hash)`。打开成功时 `report.value` 为 store；store 提供：

- `read(sessionId, {cardPackage, playerSetup})`：返回已验证私有会话或 `null`。
- `write(session, {expectedRevision, cardPackage, playerSetup})`：`expectedRevision=null` 只创建 revision 0；已有会话必须匹配当前 revision，且新 revision 恰好加 1。
- `close()`：拒绝新请求，等已排队读写结束，再核对并释放自身 owner 锁。

返回值均为 `{valid, diagnostics, value?}`。诊断只含固定代码与空 JSON Pointer，不返回正文、文件路径或底层异常。会话 `value` 含正文，只能交给被授权的私有调用方，不能当作公开验收报告。

## 检查点与单写

操作方明确配置一个本地私有目录；卡包不能提供目录。祖先链接、UNC、文件/目录链接和硬链接会被拒绝。文件名使用 `session-<sessionId>.json`，所以合同允许的逻辑 ID `con` 也能安全保存。

检查点格式为 `modelmirror.ai-rpg.runtime-checkpoint/0.1.0`，严格包含 format、formatVersion、sessionId、cardPackageSha256、playerSetupSha256、sessionSha256、session。整个会话的规范 JSON SHA-256 与资源绑定分别核对；文件包装、内层会话和请求 ID 必须一致。读取还验证旧回合合同、输出 hash、正式回合与状态重放结果。

实际读取量最多 16 MiB；锁记录最多 4096 字节。打开前后的 lstat/fstat 文件身份、类型和链接数均需一致。文件必须是规范 JSON 的 UTF-8 字节加单个 LF；BOM、重复键、未知版本、非法 UTF-8、损坏或超限内容被拒绝，不自动迁移或修补。

公开读写入口同步复制已校验输入，然后串行排队。正式回合、状态、生成和 revision 位于同一检查点。写入使用同目录独占临时文件，写完后 sync、close，重新核对 owner，再 rename 替换。失败临时文件保留供核查；已存在的无效检查点不会被正常写入覆盖。

每次打开先独占取得 `.claim.lock`，再处理覆盖 store 生命周期的 `.owner.lock`。存活 PID 或无法确认失效的 owner 会阻断打开；仅当本机确认 PID 不存在时，才归档旧 owner 并建立新 owner。未知或被替换的 claim 保留证据并阻断；释放锁前必须匹配本实例 PID/token。PID 复用为存活进程时同样拒绝接管。

这是合作式本地单写协议，不是针对同权限恶意进程的安全沙箱，也不提供网络文件系统或断电持久性保证。正文目录的操作系统访问权限由操作方管理；Windows 下不能仅凭 Node 的文件 mode 宣称已建立私有 ACL。

## 恢复

恢复必须提供原卡包、玩家配置和同步 hash 实现；资源漂移、损坏或 revision 溢出会拒绝。恢复复制会话并将 session revision 加 1。遗留 active 生成变为 interrupted，沿用持久化的 modelId/evidenceKind，保留已有草稿，未知上游信息和用量保持 null；没有模型派发或重试。

已有 pending 回合保留原 generation、完成回执与 finishedRevision；恢复只推进 session revision。只有之后显式 commit/discard 才增加 resolvedRevision。原完整提交可恢复，但不保证进程退出前最后一段流文本已持久化。

## 已运行证据

`node --test tests/runtime-store.test.mjs tests/runtime-contracts.test.mjs`：32/32 通过；主智能体独立复跑存储/恢复 13/13 通过。实际子进程写入正式回合后留下 active 生成并退出，重新打开时归档死 owner、恢复正式回合与状态，并生成 interrupted 记录。

覆盖 CAS、并发 owner、输入快照、损坏/大小/UTF-8/hash/ID、保留失败 temp、未知锁、pending 回执不变与安全整数上限。Windows 普通文件 symlink 创建受权限限制，因此悬空链接反例使用无特权 junction；硬链接反例也已执行。测试目录每次使用 mkdtemp，保留在 `.rpg03-work/runtime-store-tests/`。

文件 sync 与 rename API 参考 [Node 24 官方文档](https://nodejs.org/docs/latest-v24.x/api/fs.html)。上述结果证明本轮本地进程恢复边界，不证明 Provider、完整运行调度或真实模型验收。
