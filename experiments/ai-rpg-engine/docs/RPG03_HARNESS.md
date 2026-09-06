# RPG-03 开发 CLI 与离线 HTTP 验证

03H1 使用真正的模镜 HTTP 服务、真正的本地文件检查点和 loopback 假上游。Provider 资格由验证工具明确写入模拟数据；此结果不能证明真实供应商资格。最终脱敏回执见 `RPG03_OFFLINE_HTTP_RECEIPT.json`。

## 可复跑门禁

在模块目录使用 Node 24.18.0、npm 11.16.0 和具备父仓 requirements 的 Python 3.12 环境：

```powershell
node --test tests/runtime-cli.test.mjs
python -B scripts/rpg03-harness.py --self-test
python -B scripts/rpg03-harness.py
node scripts/check-boundary-rpg03.mjs
git diff --check
```

本次实际 Python 为已有环境的 3.12.14，uvicorn 0.32.1，未修改 Python 环境或 requirements。默认使用 `127.0.0.1:18303`；端口已占用就拒绝启动，绝不停止原占用者。验证工具拒绝 8000，假上游也仅监听 loopback。

每次生成新的 `.rpg03-work/h1-http-*`。只从 `git ls-files -- server` 复制受版本控制的候选代码，排除 `.env`、storage、uploads 和缓存。候选 `.env` 为空，以阻止向祖先目录查找配置。子进程只继承有限系统环境；全部模镜存储、临时目录和 Provider 数据库指向新目录，不复制共享数据库或凭据。关闭 lifespan，避免启动与本轮无关的后台服务；导入阶段的存储也必须隔离。

开始与结束均核对完整 tracked server 清单和 hash，以及当前 runtime、CLI、测试和包锁定文件清单。候选标识为固定基线提交加实际源码 hash，不能将未提交修改说成新的 Git 提交。最终成功回执在本工具创建的服务及假上游退出后才写入。失败保留已有文件和稳定失败回执，不覆盖旧目录；清理只针对本工具持有的子进程对象，不按端口批量结束进程。

## 已验证链路

- 原 RPG-02 代表卡包和完整五天赋玩家配置创建会话。
- 显式准备的中性测试消息经过模镜受控 Chat，得到两次有效候选，分别明确提交；第二次消息带第一次已提交回复，查询不写状态，建议动作未被执行。
- 重复 generation ID 不增加派发；关闭并重新打开文件存储后恢复两个正式回合，无模型重放。
- 第一个草稿事件触发取消，客户端报告中止；模镜数据库记录 client_cancelled，假上游观察到连接断开。运行回执仍保持 upstreamConfirmed=null，不能把独立验证工具的观察冒充上游协议确认。
- 开发 CLI 作为真实 Node 子进程完成 create、generate、commit、read；退出后另一个 CLI 进程 resume，正式回合保留且 revision 从 3 变为 4。

合计 4 次假上游派发：核心链路两次成功、一次取消，CLI 一次成功。真实 Provider 派发和网站消息探针均为 0。原代表卡包声明状态字段为空；声明字段的初始化、类型、显式应用及恢复由 03C/03D/03F 的独立状态夹具覆盖，不向代表资源补造规则。

定向 CLI 测试 5/5，真实本地 HTTP 集成测试 1/1，验证工具失败路径检查 3/3。旧合同、内容、归档及扩展 128/128 回归通过。完整 RPG-03 聚合与真实模型验收尚未完成。

## CLI 配置与交互

启动方式为 `node scripts/rpg03-cli.mjs --config <本地绝对配置路径>`。配置恰有五个字段：baseUrl、evidenceKind、sessionDirectory、cardPackagePath、playerSetupPath。前三个路径字段中的 sessionDirectory、cardPackagePath、playerSetupPath 必须是由操作方选定的本地绝对路径，不能来自卡片。evidenceKind 为 mock 或 real，只是证据分类声明；真实成功仍需服务端资格与回执。配置不允许供应商密钥或额外字段，适配器输出上限固定为 512。

stdin 每行一个 JSON 对象，恰有 requestId、operation、input。operation 为 create、read、resume、generate、cancel、commit 或 discard。前三项 input 只有 sessionId，资源由配置固定；其余 input 使用 `RPG03_RUNTIME_CONTRACTS.md` 的既有合同。requestId 必须唯一；准备消息由操作方给出，CLI 不生成主持提示词。

每行最多 1 MiB、最多 4096 行、最多 32 个未完成命令。generate 等待不会阻塞后续 cancel 的接收，但运行核心仍只允许单实例一个模型请求。依赖先前结果的命令应等待相应输出并使用返回 revision。EOF 或输入异常后先等已接纳命令完成，再关闭自己持有的存储锁；不自动重试请求。

stdout 只有经过白名单处理的逻辑 ID、状态、revision、hash、数量和未知用量。draft 事件只给长度，不打印正文。会话正文保留在配置的私有目录；公开诊断不回显消息、非法字段、路径、堆栈或密钥。输入文件逐祖先检查链接，并有实际读取字节上限及严格 UTF-8 解码。这里是开发工具，不是 RPG-05 玩家 UI，也不是长期记忆设施。
