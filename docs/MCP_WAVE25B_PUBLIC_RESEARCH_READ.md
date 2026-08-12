# Wave 25B 公共研究读取适配

## 当前结论

Wave 25B 只选择了两项能够在固定公共域名、无凭据、纯读取边界内保持原产品身份的候选：

- `karanb192-reddit-mcp-buddy`：锁定 `v1.1.14` / commit `e2d3f3fa1ab281a1ef872eeaea86134bbad2c7ec` / MIT，只开放 `browse_subreddit`、`search_reddit`。实现只访问 `www.reddit.com` 的匿名 Atom feed，不开放任意 URL、帖子详情、用户画像、评论树或账号操作。
- `rishijatia-fantasy-pl-mcp`：锁定 `v0.1.7` / commit `fdaef005143347455fc500cb1f934d451f95251a` / MIT，只开放 `search_fpl_players`、`get_player_information`、`list_fpl_fixtures`。实现只访问 `fantasy.premierleague.com` 官方公开 API，不开放登录、经理队伍、联赛、阵容、转会或建议工具。

用户验收后，Fantasy PL 已晋级 `ready` 并加入 `public_proxy.py`、Compose 与 sidecar 三处精确默认 allowlist；Reddit Buddy 继续保持 `planned`，没有可执行 manifest 或 allowlist 入口。当前 Wave 24 新增目录为 **4 ready / 42 planned / 54 blocked**，全目录为 **75 ready / 69 planned / 156 blocked**。

- Fantasy PL 已在 fresh 隔离镜像中完成两轮 initialize、tools/list、三个代表工具、拒绝未公开工具、取消超时、手动 sidecar 重启与工作目录/容器/卷精确清理，并通过用户验收。
- Reddit Buddy 的直接匿名 Atom 调用曾成功返回三条结果，但正式烟测随后命中提供方 `HTTP 429`，再次诊断时首个 feed 调用仍受限。因此不宣称真实门槛通过，不加入 allowlist，保持 `planned` 等待上游限流窗口和代表调用重新验证。

## 未纳入本单元

- `narumiruna-yfinance-mcp`、`openaccountants-openaccountants`：金融或税务高风险信息，不用于填充低风险批次。
- `takashiishida-arxiv-latex-mcp`：核心能力会下载并处理不可信论文源码归档，不能按简单公网只读 facade 验收。
- `anaisbetts-mcp-youtube`：依赖媒体提取/下载执行面，不属于固定元数据读取。
- `childrentime-reactuse`：数据随包封存，更适合 Wave 26A 离线确定性内容。
- `tonnode-mcp`：使用原生动态网络协议且属于金融/链上数据边界。
- `king-of-the-grackles-reddit-research-mcp`：仓库身份已重定向且包含托管/认证研究流程；本批不以兼容 facade 冒充。
- `patsnap-patent-literature-search-mcp`：需要 API 凭据，应进入 Token 只读预检批次。

## 固定安全边界

- 客户端不能提交 URL、Host、Header、环境变量、命令或动态 endpoint。
- Reddit 仅返回规范化 Reddit permalink 与有界文本预览；不会转发 feed 中的外部链接。Atom feed 不提供可信分数、评论数或 NSFW 标识，响应明确披露该限制。
- Fantasy PL 只投影固定球员/赛程字段，原始响应上限 2 MiB，工具结果上限 128 KiB；不生成现实决策建议。
- 两项均为 `read` effect，安全读取可由现有管理器策略处理；不存在写入、产物或状态变更。

## 验收与回退

晋级门槛为 fresh `mcp-public` 镜像的 initialize、tools/list、固定 Schema、真实代表调用、拒绝未公开工具、429、超时、断开、sidecar 重启和工作目录清理。Fantasy PL 已满足门槛；Reddit Buddy 仍因真实 429 未满足。

本轮隔离镜像为 `modelmirror-mcp-public:wave25b-v1-audit`，manifest list 为 `sha256:9d22cb24438d2e80f7c70e0e51b1dfb8dbf02d15587e59ca836a5ce23719c5a1`。Fantasy PL 两轮均返回 3 个球员、3 场赛程，Schema digest 为 `b9760cc0e80c3c906a96e9090e90c57e31b4443e2f58a622c6769ee8448fe602`；每轮结束后工作目录为空，最终临时容器和卷均为零。测试环境必须显式开启既有 synthetic-DNS 开关，生产 Compose 未开启且仍 fail-closed。

回退不涉及数据迁移：Fantasy PL 只需从三处精确 allowlist 和目录运行时契约移除并恢复为 `planned`；Reddit Buddy 保持 `planned`。两项均没有持久数据需要迁移或清理。
