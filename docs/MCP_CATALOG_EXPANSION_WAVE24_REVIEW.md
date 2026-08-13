# MCP 目录扩充 Wave 24 判定

快照日期：2026-08-11

## 结论

- 用户已批准本次固定 100 项清单进入静态产品目录。
- 判定：`8 ready / 34 planned / 58 blocked`。
- Wave 24 首次导入不创建 ready；后续只有完成真实运行证据与用户验收的精确 ID 才能晋级。
- Wave 25–30 已完成验收的八项已晋级；命令、工具策略与 allowlist 仍由服务端显式冻结，不由生成器产生。
- 与原有 200 项合并后产品目录总数保持 300。

## 固定来源

- `yzfly/Awesome-MCP-ZH@b29e114d95fa26338b092423fd1ede1e5598e4df`，README SHA256 `854802528cb508a6f6d00e2d142b57a44bc5393bfd4321ddd96e1e9a2b10b51a`。
- `punkpeye/awesome-mcp-servers@cbcdf8f7700cfe4c0ef9aeb232f64aeebe8a184c`，README SHA256 `d7012abf5a5019f2ff0b66dff3832b2b0c1e8c9dd672f382f3ae677d3b878874`。

## 判定清单

| 排名 | 项目 | 类别 | 状态 | 目标批次/原因码 |
| ---: | --- | --- | --- | --- |
| 1 | `yepcode-mcp-server-js` | 开发与代码 | blocked | Wave 24 / `blocked-arbitrary-command-code-or-target` |
| 2 | `ergut-mcp-bigquery-server` | 数据库 | planned | Wave 26 / `planned-wave26-token-readonly-preflight` |
| 3 | `executeautomation-mcp-playwright` | 浏览器与网页 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 4 | `fradser-mcp-server-apple-reminders` | 效率与协作 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 5 | `githejie-mcp-server-calculator` | 通用工具 | ready | Wave 26 / `ready-wave26a-calculator` |
| 6 | `modelscope-funasr` | 多媒体 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 7 | `muvon-octocode` | 开发与代码 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 8 | `narumiruna-yfinance-mcp` | 金融与市场 | blocked | Wave 29 / `blocked-provider-data-terms` |
| 9 | `pspdfkit-nutrient-dws-mcp-server` | 文件与存储 | planned | Wave 26 / `planned-wave26-token-readonly-preflight` |
| 10 | `rohitg00-kubectl-mcp-server` | 云平台与运维 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 11 | `supermemoryai-supermemory` | 知识与记忆 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 12 | `takashiishida-arxiv-latex-mcp` | 搜索与研究 | ready | Wave 29 / `ready-wave29-arxiv-public-read` |
| 13 | `tumf-mcp-shell-server` | 开发与代码 | blocked | Wave 24 / `blocked-arbitrary-command-code-or-target` |
| 14 | `zcaceres-fetch-mcp` | 开发与代码 | blocked | Wave 24 / `blocked-arbitrary-command-code-or-target` |
| 15 | `zcaceres-gtasks-mcp` | 通讯与协作 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 16 | `chanmeng666-server-google-news` | 搜索与研究 | planned | Wave 26 / `planned-wave26-token-readonly-preflight` |
| 17 | `coinpaprika-dexpaprika-mcp` | 金融与市场 | ready | Wave 25 / `ready-wave25-public-read` |
| 18 | `nkapila6-mcp-local-rag` | 搜索与研究 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 19 | `secretiveshell-mcp-searxng` | 搜索与研究 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 20 | `weibaohui-k8m` | 云平台与运维 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 21 | `zenml-io-mcp-zenml` | 开发与代码 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 22 | `anaisbetts-mcp-youtube` | 多媒体 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 23 | `apinetwork-piapi-mcp-server` | 多媒体 | blocked | Wave 24 / `blocked-paid-generation-transaction-or-wallet` |
| 24 | `apollographql-apollo-mcp-server` | 开发与代码 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 25 | `greptimeteam-greptimedb-mcp-server` | 数据库 | ready | Wave 28 / `ready-wave28-greptimedb-readonly` |
| 26 | `joshuayoes-ios-simulator-mcp` | 开发与代码 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 27 | `utensils-mcp-nixos` | 开发与代码 | blocked | Wave 25 / `blocked-wave25-public-backend-requires-embedded-credential` |
| 28 | `callstackincubator-agent-device` | 开发与代码 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 29 | `dbt-labs-dbt-mcp` | 数据分析 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 30 | `eat-pray-ai-yutu` | 浏览器与网页 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 31 | `flytohub-flyto-core` | 开发与代码 | blocked | Wave 24 / `blocked-arbitrary-command-code-or-target` |
| 32 | `freema-openclaw-mcp` | 开发与代码 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 33 | `higangssh-homebutler` | 云平台与运维 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 34 | `jau123-meigen-ai-design-mcp` | 多媒体 | blocked | Wave 24 / `blocked-paid-generation-transaction-or-wallet` |
| 35 | `klavis-ai-klavis` | 通用工具 | blocked | Wave 24 / `blocked-dynamic-integration-or-security-control-plane` |
| 36 | `korotovsky-slack-mcp-server` | 通讯与协作 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 37 | `mckinsey-vizro` | 数据分析 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 38 | `mobilereality-mdma` | 开发与代码 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 39 | `planetscale-cli` | 数据库 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 40 | `pydantic-pydantic-ai` | 开发与代码 | blocked | Wave 24 / `blocked-arbitrary-command-code-or-target` |
| 41 | `repowise-dev-repowise` | 开发与代码 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 42 | `runapi-ai-mcp` | 多媒体 | blocked | Wave 24 / `blocked-paid-generation-transaction-or-wallet` |
| 43 | `anki-mcp-anki-mcp-desktop` | 通用工具 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 44 | `bgauryy-octocode-mcp` | 开发与代码 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 45 | `childrentime-reactuse` | 云平台与运维 | planned | Wave 25 / `planned-wave25-anonymous-public-read-contract` |
| 46 | `lvcidpsyche-auto-browser` | 浏览器与网页 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 47 | `openfate-ai-bazi-mcp` | 通用工具 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 48 | `polygon-io-mcp-polygon` | 金融与市场 | planned | Wave 26 / `planned-wave26-token-readonly-preflight` |
| 49 | `scottcjn-rustchain-mcp` | 金融与市场 | blocked | Wave 24 / `blocked-paid-generation-transaction-or-wallet` |
| 50 | `tonnode-mcp` | 金融与市场 | planned | Wave 25 / `planned-wave25-anonymous-public-read-contract` |
| 51 | `writerslogic-scrivener-mcp` | 知识与记忆 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 52 | `gomarble-ai-facebook-ads-mcp-server` | 社交与内容 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 53 | `healthchainai-healthchain` | 搜索与研究 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 54 | `isnow890-naver-search-mcp` | 搜索与研究 | planned | Wave 26 / `planned-wave26-token-readonly-preflight` |
| 55 | `karanb192-reddit-mcp-buddy` | 社交与内容 | planned | Wave 25 / `planned-wave25-anonymous-public-read-contract` |
| 56 | `kunwar-shah-claudex` | 知识与记忆 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 57 | `kzino-vorim-mcp-server` | 安全分析 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 58 | `mariocandela-beelzebub` | 安全分析 | blocked | Wave 24 / `blocked-dynamic-integration-or-security-control-plane` |
| 59 | `nameetp-pdfmux` | 搜索与研究 | blocked | Wave 29 / `blocked-license-runtime-dependency` |
| 60 | `openaccountants-openaccountants` | 金融与市场 | planned | Wave 25 / `planned-wave25-anonymous-public-read-contract` |
| 61 | `pab1it0-chess-mcp` | 通用工具 | ready | Wave 25 / `ready-wave25-public-read` |
| 62 | `rashidazarang-airtable-mcp` | 数据库 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 63 | `rishijatia-fantasy-pl-mcp` | 通用工具 | ready | Wave 25 / `ready-wave25-public-read` |
| 64 | `rusiaaman-wcgw` | 通用工具 | blocked | Wave 24 / `blocked-arbitrary-command-code-or-target` |
| 65 | `snowflake-labs-mcp` | 数据库 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 66 | `stabgan-openrouter-mcp-multimodal` | 多媒体 | blocked | Wave 24 / `blocked-paid-generation-transaction-or-wallet` |
| 67 | `sunriseapps-imagesorcery-mcp` | 多媒体 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 68 | `xeroapi-xero-mcp-server` | 金融与市场 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 69 | `yuna0x0-anilist-mcp` | 通用工具 | ready | Wave 25 / `ready-wave25-public-read` |
| 70 | `agenticmail-agenticmail` | 通讯与协作 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 71 | `bintocher-mcp-superset` | 数据分析 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 72 | `chemiguel23-memorymesh` | 知识与记忆 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 73 | `chroma-core-chroma-mcp` | 数据库 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 74 | `codeabra-iai-personal-memory-engine` | 知识与记忆 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 75 | `confluentinc-mcp-confluent` | 数据库 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 76 | `frowningdev-django-orm-lens` | 数据库 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 77 | `jtang613-ghidrassistmcp` | 安全分析 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 78 | `king-of-the-grackles-reddit-research-mcp` | 社交与内容 | planned | Wave 25 / `planned-wave25-anonymous-public-read-contract` |
| 79 | `mark3labs-mcp-filesystem-server` | 文件与存储 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 80 | `mnemox-ai-tradememory-protocol` | 金融与市场 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 81 | `nakaokarei-swift-mcp-gui` | 通用工具 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 82 | `ndthanhdev-mcp-browser-kit` | 浏览器与网页 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 83 | `openmf-mcp-mifosx` | 金融与市场 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 84 | `overpod-mcp-telegram` | 通讯与协作 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 85 | `patsnap-patent-literature-search-mcp` | 搜索与研究 | planned | Wave 25 / `planned-wave25-anonymous-public-read-contract` |
| 86 | `portainer-portainer-mcp` | 云平台与运维 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 87 | `quackbackio-quackback` | 数据分析 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 88 | `riponcm-projectmem` | 知识与记忆 | planned | Wave 21 / `planned-wave21-stateful-foundation-required` |
| 89 | `stape-io-google-tag-manager-mcp-server` | 社交与内容 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 90 | `the-momentum-apple-health-mcp-server` | 搜索与研究 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 91 | `traceloop-opentelemetry-mcp-server` | 金融与市场 | planned | Wave 27 / `planned-wave27-native-readonly-data-service` |
| 92 | `victoriametrics-community-mcp-victoriametrics` | 云平台与运维 | ready | Wave 30 / `ready-wave30-victoriametrics-readonly` |
| 93 | `workopia-workopia-mcp` | 效率与协作 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 94 | `yuna0x0-hackmd-mcp` | 效率与协作 | blocked | Wave 24 / `blocked-account-cloud-write-or-management` |
| 95 | `yurineko73-godot-mcp-native` | 通用工具 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 96 | `yusufkaraaslan-skill-seekers` | 知识与记忆 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 97 | `zinja-coder-apktool-mcp-server` | 安全分析 | planned | Wave 26 / `planned-wave26-offline-file-or-deterministic-artifact` |
| 98 | `zubeidhendricks-youtube-mcp-server` | 多媒体 | blocked | Wave 24 / `blocked-superseded-existing-controlled-capability` |
| 99 | `ai-xiaodao-ai-browser-mcp` | 浏览器与网页 | blocked | Wave 24 / `blocked-desktop-browser-or-device-control` |
| 100 | `aimino-tech-opendocswork-mcp` | 文件与存储 | blocked | Wave 29 / `blocked-license-metadata-conflict` |

## 回退

Wave 25–30 回退时移除对应精确 allowlist/runtime contract 并将其恢复为 planned/blocked；目录导入本身没有数据迁移。
