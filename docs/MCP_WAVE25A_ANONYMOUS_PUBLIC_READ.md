# Wave 25A 匿名公共读取适配

最后更新日期：2026-08-12

## 1. 结论

Wave 25A 从 Wave 24 的候选中复核四项匿名公共读取能力：

- `coinpaprika-dexpaprika-mcp`、`pab1it0-chess-mcp`、`yuna0x0-anilist-mcp` 已完成固定契约、真实公共上游代表调用、超时、拒写、重启、清理和用户验收，现已晋级 `ready` 并进入三个精确公共 allowlist。
- `utensils-mcp-nixos` 转为 `blocked`：固定 v3.0.0 使用的 NixOS 搜索后端对匿名访问返回 401，且上游源码包含 Basic Auth 凭据。本项目不复制该凭据、不新增凭据槽，也不更换后端冒充原产品。
- `public_proxy.py`、public sidecar 默认 allowlist、生产 Compose allowlist 和目录运行时 manifest 只增加这三个精确 ID；没有开放其他 Wave 24 项。

Wave 25B 后续又验收并晋级 Fantasy PL，因此当前 Wave 24 新增目录为 **4 ready / 42 planned / 54 blocked**，全目录为 **75 ready / 69 planned / 156 blocked**。Wave 25A 本身的三个精确 ID 和回退边界保持不变。

## 2. 固定身份与公开工具

| 项目 | 固定上游 | 许可证 | 固定出口 | 公开工具 | Schema SHA-256 |
|---|---|---|---|---|---|
| DexPaprika MCP | `coinpaprika/dexpaprika-mcp` v2.3.2，commit `02bfbcc8e0468d3a82d9e060e5da398a0d22f23c` | MIT | `api.dexpaprika.com` | `getNetworks`、`getStats`、`search` | `b6b6a6ef17aed4544341be76648401fd4ac6a62f4d657d9f5da0f2429429ebc9` |
| Chess MCP | `pab1it0/chess-mcp` v0.1.0，commit `3f4068ed6befe0be34c4cef3e7e5e9234ebc3a3d` | MIT | `api.chess.com` | `get_player_profile`、`get_player_stats` | `d33380c3a2cd3e271e289c9a021c1c8d67403bb2f74a4c5df6e075b67882cf7d` |
| AniList MCP | `yuna0x0/anilist-mcp` v1.4.0，commit `7c5cf1e374c09e3ddbd9c68f92c4c08a43e65477` | MIT | `graphql.anilist.co` | `get_genres`、`search_anime`、`get_anime` | `060e2a7e6eb92fd44a945b99ca91adb614eb877e286535516b9ec8c0a7b7e239` |

兼容层只保留原产品的匿名读取身份，不运行任意上游命令，不接受 URL、Host、Header、环境变量、代理或动态 MCP endpoint。所有查询和返回数量均有固定上限；AniList 只发送仓库内冻结的 GraphQL 查询文档，不接受客户端 GraphQL。

明确不可发现、不可调用的代表工具包括：

- DexPaprika `submitFeedback`；
- Chess `download_player_games_pgn`；
- AniList `favourite_anime`。

Chess 上游声明的 `is_player_online` 在当前固定公共 API 路径真实返回 404。为避免用兼容 facade 掩盖上游漂移，该工具已从本批契约中移除，而不是伪造结果。

## 3. 隔离验收证据

隔离镜像 `modelmirror-mcp-public:wave25a-v1-audit`：

- manifest list：`sha256:0d0614ddca6bf1368d2791fad957194addb739348c06996e814ac8abab10d1f1`
- config：`sha256:2a773f1090a838f101092853e8ec07e5a9e9b8c854ed78b105406025c72466a4`

真实公网双轮验收均通过，并在两轮之间重启 sidecar：

- DexPaprika：36 个 network、36 个 chain、2 个搜索 token 结果；
- Chess：固定公开账号 profile 与 5 类 rating 统计；
- AniList：19 个 genre、2 个搜索结果、固定详情 ID；
- 三项均验证写/下载工具拒绝、调用超时取消、会话断开和 socket 清理；
- sidecar `StartedAt` 在重启后变化；验收资源最终为 `containers=0`、`volumes=0`。

容器边界保持 UID/GID 65532、只读根文件系统、`CapDrop=ALL`、`no-new-privileges`、128 PIDs、512 MiB，并仅使用独立 bridge 网络。共享栈未启动或重建。

## 4. 晋级与回退

用户已验收三个候选，并已完成以下精确变更：

1. 三个项目的目录状态为 `ready`，并写入逐工具 `read` 策略；
2. 三个精确 ID 已进入 `public_proxy.py`、public sidecar 默认 allowlist 和 Compose allowlist；
3. 晋级后继续重跑目录计数、公共 sidecar 回归、前端检查和 Compose 解析。

回退不需要数据迁移：移除三个精确 allowlist ID、将对应 manifest 恢复为 `planned` 并断开相关目录会话即可。MCP-NixOS 保持 `blocked`，除非上游提供可验证、无需复制嵌入凭据的匿名官方后端。
