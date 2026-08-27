# R4B 认证型远程 MCP Runtime 验收记录

## 基线与隔离环境

- 验证日期：`2026-08-27`。
- 真实预览基线：`origin/main@27ab7de9e1256a7c26139a528701bfb85f707f94`。
- PR 最终集成基线：`origin/main@bf486f25`。随后合并的 PR #317 只新增 Skill Creator 晋级能力；与 R4B 唯一同文件为 `server/main.py`，修改区段不同并已无冲突重放。
- 工作树：`C:\tmp\modelmirror-mcp-remote-unification-r4b-runtime`。
- 独立预览：`http://127.0.0.1:15286`；Compose 项目：`modelmirror-r4b-runtime-preview`。
- 未重建共享栈；只使用本轮隔离容器、临时 SQLite 和独立端口。

## 真实三路径闭环

### Catalog static-token：GitHub

- `github-mcp-server` 完成两次逐次审批 Runtime 读取。
- binding 依次完成创建 revision 1、旋转 revision 2、撤销 revision 3。
- revision 变化后目标进入 drifted，旧 Runtime binding 被移除；旧批准不可重放。

### Catalog OAuth：Tako

- `tako-mcp` 完成一次逐次审批 Runtime 读取。
- 远程 refresh token 撤销完成；目标状态为 revoked，Runtime binding 被移除。

### Hub OAuth：Tako

- 逐次批准 `tako_available_data({"q":"Nvidia"})`，只派发一次且 `retry_on_failure=false`。
- 结果大小 `10716` 字节，SHA256 为 `fe9bb9ee2a8fb61369a756939e8e2766e8b677dd60e8dc7ae78089388b830789`，execution ledger 状态为 completed。
- 远程 refresh token 撤销完成；candidate 已断开，撤销后的 Runtime 工具数为 0。

## 预览器复核与截图

在 `/mcps` 搜索 Tako，打开“认证与复核”。撤销后页面显示：

- 复核状态：已撤销。
- 运行边界：未激活；未复核目标不会进入 Runtime。
- “激活 Runtime”按钮不可用。
- 已发布契约仍可导出审计；页面没有 Token、授权码、client ID、用户信息或本机密钥。

截图：`client/public/help-center/27ab7de9/catalog-tako-runtime-revoked.png`

SHA256：`EFF0714D638696103F7F249325B7327A3E07E61927E0A8A081E8338AA0E9429E`

## 自动化与隔离门禁

- R4B 定向后端：`233 passed, 5 warnings`。
- 新同步 RAG 定向回归：`66 passed, 4 warnings`。
- Workflow contract：`6 passed`。
- CI 等价完整后端：`4965 passed, 29 skipped, 6 warnings`。
- 前端 typecheck 与生产 build：通过；完整前端：`122 files / 744 tests passed`。
- orchestration worker：build 通过；本地 `75` 项和 upstream 套件全部通过。
- `docker compose config --quiet` 与 `git diff --check`：通过。
- remote 与 OAuth sidecar 均为 UID `65532:65532`、只读根、`network_mode:none`、`cap-drop=ALL`、`no-new-privileges:true`；egress 使用专用网络且不接触 Token。
- 同步 PR #317 后，R4B 变更测试与新主线 Skill 晋级测试合计 `78 passed, 4 warnings`。

完整后端绿测完成后，主线新增的 RAG 提交与 R4B 零文件交叉；同步后已额外复跑 R4B 定向测试和新增 RAG 定向测试。PR #317 的 `server/main.py` 非交叉区段也已通过上述联合回归。最终 PR SHA 的完整矩阵仍由 CI 再次执行。

## 已验证边界与回退

- 当前只验证 `local/local` 单主体，不代表多租户 RBAC 或对象隔离完成。
- 只实测人工确认的读取工具；写入、管理、删除、发布、交易和设备控制保持阻断。
- 凭据只存在于本地加密槽和短会话内存，未写入 env、仓库、截图或报告。
- 工具调用不自动重试；派发后断链按 `unknown_outcome` 收口并取消激活。
- 关闭 `MCP_REMOTE_CONTRACT_RUNTIME_ENABLED` 与 `MCP_REMOTE_CATALOG_RUNTIME_ENABLED` 即可停止新 Runtime 路径；V1–V3 Hub 契约和普通 Catalog 适配器继续工作。
