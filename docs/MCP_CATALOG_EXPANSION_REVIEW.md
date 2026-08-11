# MCP 双源目录扩充适配判定

审查快照日期：2026-08-09

> 100 项已经逐项归入 `ready`、`planned` 或 `blocked`。只有通过固定工具、隔离和代表调用门槛的条目可执行。
> 非 ready 条目不包含命令、端点、凭据槽、工具策略或功能开关绕过路径。

## 固定来源

- `awesome-mcp-zh`：`b29e114d95fa26338b092423fd1ede1e5598e4df`，README SHA-256 `854802528cb508a6f6d00e2d142b57a44bc5393bfd4321ddd96e1e9a2b10b51a`
- `awesome-mcp-servers`：`cbcdf8f7700cfe4c0ef9aeb232f64aeebe8a184c`，README SHA-256 `d7012abf5a5019f2ff0b66dff3832b2b0c1e8c9dd672f382f3ae677d3b878874`

## 结果

- 上游解析记录：3980
- 唯一仓库/子包：3542
- 结构预筛后的新候选：3251
- 完成适配判定：100
- 覆盖分类：17
- 中文源命中：71
- 英文源命中：99
- 本批状态：23 ready / 17 planned / 60 blocked
- 新增执行能力：22（批次 14—19A 的固定只读/确定性产物子集）

硬门禁：公开仓库存在，未归档/禁用/私有/派生，许可证 SPDX 明确，且最近 12 个月有推送。每个分类最多 15 项，每个仓库最多 2 项。

## 100 项适配判定

| 排名 | 目录 ID | 仓库 | 分类 | Stars | 许可证 | 状态 | 原因码 |
| ---: | --- | --- | --- | ---: | --- | --- | --- |
| 1 | `deusdata-codebase-memory-mcp` | [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | 开发与代码 | 38297 | MIT | blocked | `blocked-superseded-code-index-implementation` |
| 2 | `0xmassi-webclaw` | [0xMassi/webclaw](https://github.com/0xMassi/webclaw) | 浏览器与网页 | 2123 | AGPL-3.0 | blocked | `blocked-arbitrary-browser-or-url-surface` |
| 3 | `co-browser-browser-use-mcp-server` | [kontext-security/browser-use-mcp-server](https://github.com/kontext-security/browser-use-mcp-server) | 浏览器与网页 | 842 | MIT | blocked | `blocked-arbitrary-browser-or-url-surface` |
| 4 | `sipyourdrink-ltd-bernstein` | [sipyourdrink-ltd/bernstein](https://github.com/sipyourdrink-ltd/bernstein) | 开发与代码 | 820 | Apache-2.0 | blocked | `blocked-arbitrary-command-or-code-execution` |
| 5 | `beever-ai-beever-atlas` | [Beever-AI/beever-atlas](https://github.com/Beever-AI/beever-atlas) | 知识与记忆 | 437 | Apache-2.0 | planned | `planned-wave21-stateful-foundation-required` |
| 6 | `googleapis-genai-toolbox` | [googleapis/mcp-toolbox](https://github.com/googleapis/mcp-toolbox) | 数据库 | 16140 | Apache-2.0 | blocked | `blocked-dynamic-database-control-plane` |
| 7 | `flox-foundation-flox-mcp` | [FLOX-Foundation/flox](https://github.com/FLOX-Foundation/flox) | 金融与市场 | 219 | MIT | blocked | `blocked-financial-or-transactional-write` |
| 8 | `us-crw` | [us/crw](https://github.com/us/crw) | 浏览器与网页 | 549 | AGPL-3.0 | blocked | `blocked-arbitrary-host-or-target-surface` |
| 9 | `fatwang2-search1api-mcp` | [superagents-lab/search1api-mcp](https://github.com/superagents-lab/search1api-mcp) | 搜索与研究 | 173 | MIT | ready | `ready-official-native-discovery-only-facade` |
| 10 | `markuspfundstein-mcp-obsidian` | [MarkusPfundstein/mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian) | 效率与协作 | 4283 | MIT | blocked | `blocked-arbitrary-host-or-target-surface` |
| 11 | `mrexodia-ida-pro-mcp` | [mrexodia/ida-pro-mcp](https://github.com/mrexodia/ida-pro-mcp) | 安全分析 | 11210 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 12 | `qdrant-mcp-server-qdrant` | [qdrant/mcp-server-qdrant](https://github.com/qdrant/mcp-server-qdrant) | 数据库 | 1494 | Apache-2.0 | ready | `ready-isolated-readonly-data-service-facade` |
| 13 | `wonderwhy-er-desktopcommandermcp` | [wonderwhy-er/DesktopCommanderMCP](https://github.com/wonderwhy-er/DesktopCommanderMCP) | 开发与代码 | 9284 | MIT | blocked | `blocked-arbitrary-command-or-code-execution` |
| 14 | `blazickjp-arxiv-mcp-server` | [blazickjp/arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) | 搜索与研究 | 3035 | Apache-2.0 | ready | `ready-native-read-only-metadata-facade` |
| 15 | `goldentrii-agentrecall` | [Goldentrii/AgentRecall-X](https://github.com/Goldentrii/AgentRecall-X) | 知识与记忆 | 364 | MIT | planned | `planned-wave21-stateful-foundation-required` |
| 16 | `zcaceres-markdownify-mcp` | [zcaceres/markdownify-mcp](https://github.com/zcaceres/markdownify-mcp) | 数据分析 | 2908 | MIT | ready | `ready-isolated-deterministic-file-artifact-facade` |
| 17 | `benborla-mcp-server-mysql` | [benborla/mcp-server-mysql](https://github.com/benborla/mcp-server-mysql) | 数据库 | 2021 | MIT | blocked | `blocked-superseded-existing-capability` |
| 18 | `samuelgursky-davinci-resolve-mcp` | [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) | 多媒体 | 2060 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 19 | `aas-ee-open-websearch` | [Aas-ee/open-webSearch](https://github.com/Aas-ee/open-webSearch) | 搜索与研究 | 1691 | Apache-2.0 | ready | `ready-native-fixed-engine-search-facade` |
| 20 | `chigwell-telegram-mcp` | [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) | 通讯与协作 | 1433 | Apache-2.0 | blocked | `blocked-broad-account-or-message-write` |
| 21 | `codergamester-mcp-unity` | [CoderGamester/mcp-unity](https://github.com/CoderGamester/mcp-unity) | 通用工具 | 1862 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 22 | `flux159-mcp-server-kubernetes` | [Flux159/mcp-server-kubernetes](https://github.com/Flux159/mcp-server-kubernetes) | 云平台与运维 | 1522 | MIT | blocked | `blocked-privileged-infrastructure-write` |
| 23 | `genomoncology-biomcp` | [genomoncology/biomcp](https://github.com/genomoncology/biomcp) | 搜索与研究 | 581 | MIT | ready | `ready-native-anonymous-biomedical-metadata-facade` |
| 24 | `nickclyde-duckduckgo-mcp-server` | [nickclyde/duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server) | 搜索与研究 | 1405 | MIT | ready | `ready-native-anonymous-search-facade` |
| 25 | `oraios-serena` | [oraios/serena](https://github.com/oraios/serena) | 开发与代码 | 27782 | MIT | blocked | `blocked-arbitrary-command-or-code-execution` |
| 26 | `taylorwilsdon-google-workspace-mcp` | [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp) | 效率与协作 | 2991 | MIT | blocked | `blocked-broad-account-or-message-write` |
| 27 | `mnemox-ai-idea-reality-mcp` | [mnemox-ai/idea-reality-mcp](https://github.com/mnemox-ai/idea-reality-mcp) | 搜索与研究 | 775 | MIT | ready | `ready-native-public-idea-research-facade` |
| 28 | `chopratejas-headroom` | [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom) | 知识与记忆 | 65654 | Apache-2.0 | planned | `planned-wave21-stateful-foundation-required` |
| 29 | `designcomputer-mysql-mcp-server` | [designcomputer/mysql_mcp_server](https://github.com/designcomputer/mysql_mcp_server) | 数据库 | 1353 | MIT | blocked | `blocked-superseded-existing-capability` |
| 30 | `freepeak-db-mcp-server` | [FreePeak/db-mcp-server](https://github.com/FreePeak/db-mcp-server) | 数据库 | 409 | MIT | blocked | `blocked-superseded-existing-capability` |
| 31 | `ihor-sokoliuk-mcp-searxng` | [ihor-sokoliuk/mcp-searxng](https://github.com/ihor-sokoliuk/mcp-searxng) | 搜索与研究 | 1105 | MIT | blocked | `blocked-arbitrary-host-or-target-surface` |
| 32 | `inditextech-mcp-teams-server` | [InditexTech/mcp-teams-server](https://github.com/InditexTech/mcp-teams-server) | 通讯与协作 | 390 | Apache-2.0 | blocked | `blocked-broad-account-or-message-write` |
| 33 | `kagisearch-kagimcp` | [kagisearch/kagimcp](https://github.com/kagisearch/kagimcp) | 搜索与研究 | 472 | MIT | ready | `ready-official-native-read-only-api-facade` |
| 34 | `mcpdotdirect-evm-mcp-server` | [mcpdotdirect/evm-mcp-server](https://github.com/mcpdotdirect/evm-mcp-server) | 金融与市场 | 381 | MIT | blocked | `blocked-financial-or-transactional-write` |
| 35 | `shashankss1205-codegraphcontext` | [CodeGraphContext/CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext) | 开发与代码 | 4058 | MIT | blocked | `blocked-superseded-code-index-implementation` |
| 36 | `tuanle96-mcp-odoo` | [erpipe-org/mcp-odoo](https://github.com/erpipe-org/mcp-odoo) | 效率与协作 | 384 | MIT | blocked | `blocked-broad-account-or-message-write` |
| 37 | `ckreiling-mcp-server-docker` | [ckreiling/mcp-server-docker](https://github.com/ckreiling/mcp-server-docker) | 开发与代码 | 736 | GPL-3.0 | blocked | `blocked-arbitrary-command-or-code-execution` |
| 38 | `coding-solo-godot-mcp` | [Coding-Solo/godot-mcp](https://github.com/Coding-Solo/godot-mcp) | 通用工具 | 5138 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 39 | `cyberchitta-llm-context-py` | [cyberchitta/llm-context.py](https://github.com/cyberchitta/llm-context.py) | 文件与存储 | 305 | Apache-2.0 | ready | `ready-isolated-file-analysis-facade` |
| 40 | `haris-musa-excel-mcp-server` | [haris-musa/excel-mcp-server](https://github.com/haris-musa/excel-mcp-server) | 数据分析 | 4096 | MIT | ready | `ready-isolated-file-analysis-facade` |
| 41 | `idosal-git-mcp` | [idosal/git-mcp](https://github.com/idosal/git-mcp) | 开发与代码 | 8319 | Apache-2.0 | ready | `ready-native-canonical-github-repository-facade` |
| 42 | `nteract-semiotic` | [nteract/semiotic](https://github.com/nteract/semiotic) | 数据分析 | 2691 | Apache-2.0 | blocked | `blocked-not-an-executable-mcp-server` |
| 43 | `public-ui-kolibri` | [public-ui/kolibri](https://github.com/public-ui/kolibri) | 开发与代码 | 274 | EUPL-1.2 | blocked | `blocked-not-an-executable-mcp-server` |
| 44 | `radareorg-radare2-mcp` | [radareorg/radare2-mcp](https://github.com/radareorg/radare2-mcp) | 安全分析 | 283 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 45 | `skyhook-io-radar` | [skyhook-io/radar](https://github.com/skyhook-io/radar) | 开发与代码 | 2763 | Apache-2.0 | blocked | `blocked-privileged-infrastructure-write` |
| 46 | `suekou-mcp-notion-server` | [suekou/mcp-notion-server](https://github.com/suekou/mcp-notion-server) | 效率与协作 | 919 | MIT | blocked | `blocked-superseded-unbounded-third-party` |
| 47 | `xing5-mcp-google-sheets` | [xing5/mcp-google-sheets](https://github.com/xing5/mcp-google-sheets) | 数据库 | 979 | MIT | blocked | `blocked-broad-account-or-message-write` |
| 48 | `comet-ml-opik-mcp` | [comet-ml/opik-mcp](https://github.com/comet-ml/opik-mcp) | 数据分析 | 216 | Apache-2.0 | planned | `planned-real-account-readonly-preflight-required` |
| 49 | `quarkiverse-quarkus-mcp-servers-filesystem` | [quarkiverse/quarkus-mcp-servers](https://github.com/quarkiverse/quarkus-mcp-servers) | 文件与存储 | 195 | Apache-2.0 | blocked | `blocked-superseded-existing-capability` |
| 50 | `quarkiverse-quarkus-mcp-servers-jdbc` | [quarkiverse/quarkus-mcp-servers](https://github.com/quarkiverse/quarkus-mcp-servers) | 数据库 | 195 | Apache-2.0 | blocked | `blocked-superseded-existing-capability` |
| 51 | `samvallad33-vestige` | [samvallad33/vestige](https://github.com/samvallad33/vestige) | 知识与记忆 | 603 | AGPL-3.0 | planned | `planned-wave21-stateful-foundation-required` |
| 52 | `vivekvells-mcp-pandoc` | [vivekVells/mcp-pandoc](https://github.com/vivekVells/mcp-pandoc) | 开发与代码 | 573 | MIT | ready | `ready-isolated-deterministic-file-artifact-facade` |
| 53 | `yolfinance-yolfi-agent` | [yolfinance/yolfi-agent](https://github.com/yolfinance/yolfi-agent) | 金融与市场 | 198 | MIT | blocked | `blocked-financial-or-transactional-write` |
| 54 | `zilliztech-mcp-server-milvus` | [zilliztech/mcp-server-milvus](https://github.com/zilliztech/mcp-server-milvus) | 数据库 | 238 | Apache-2.0 | planned | `planned-read-only-data-facade` |
| 55 | `alexei-led-k8s-mcp-server` | [alexei-led/k8s-mcp-server](https://github.com/alexei-led/k8s-mcp-server) | 云平台与运维 | 213 | MIT | blocked | `blocked-privileged-infrastructure-write` |
| 56 | `anypost-emailmd` | [anypost/emailmd](https://github.com/anypost/emailmd) | 通讯与协作 | 1297 | MIT | blocked | `blocked-publishing-or-high-risk-advice` |
| 57 | `brave-brave-search-mcp-server` | [brave/brave-search-mcp-server](https://github.com/brave/brave-search-mcp-server) | 搜索与研究 | 1362 | MIT | ready | `ready-official-read-only-token-contract` |
| 58 | `cablate-mcp-google-map` | [cablate/mcp-google-map](https://github.com/cablate/mcp-google-map) | 地理与出行 | 419 | MIT | planned | `planned-real-account-readonly-preflight-required` |
| 59 | `diivi-aseprite-mcp` | [diivi/aseprite-mcp](https://github.com/diivi/aseprite-mcp) | 多媒体 | 399 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 60 | `docker-hub-mcp` | [docker/hub-mcp](https://github.com/docker/hub-mcp) | 开发与代码 | 159 | Apache-2.0 | ready | `ready-official-native-anonymous-metadata-facade` |
| 61 | `integromat-make-mcp-server` | [integromat/make-mcp-server](https://github.com/integromat/make-mcp-server) | 效率与协作 | 166 | MIT | blocked | `blocked-broad-account-or-message-write` |
| 62 | `ivanmurzak-unity-mcp` | [IvanMurzak/Unity-MCP](https://github.com/IvanMurzak/Unity-MCP) | 通用工具 | 3845 | Apache-2.0 | blocked | `blocked-desktop-host-instance-unverified` |
| 63 | `juyterman1000-entroly` | [juyterman1000/entroly](https://github.com/juyterman1000/entroly) | 知识与记忆 | 435 | Apache-2.0 | planned | `planned-wave21-stateful-foundation-required` |
| 64 | `livetennisapi-livetennisapi-mcp` | [livetennisapi/livetennisapi-mcp](https://github.com/livetennisapi/livetennisapi-mcp) | 通用工具 | 190 | MIT | ready | `ready-official-native-free-read-only-facade` |
| 65 | `neo4j-contrib-mcp-neo4j` | [neo4j-contrib/mcp-neo4j](https://github.com/neo4j-contrib/mcp-neo4j) | 数据库 | 979 | MIT | planned | `planned-read-only-data-facade` |
| 66 | `pab1it0-prometheus-mcp-server` | [pab1it0/prometheus-mcp-server](https://github.com/pab1it0/prometheus-mcp-server) | 数据库 | 512 | MIT | ready | `ready-isolated-readonly-data-service-facade` |
| 67 | `pv-bhat-vibe-check-mcp-server` | [PV-Bhat/vibe-check-mcp-server](https://github.com/PV-Bhat/vibe-check-mcp-server) | 通用工具 | 503 | MIT | planned | `planned-wave21-stateful-foundation-required` |
| 68 | `r-huijts-strava-mcp` | [r-huijts/strava-mcp](https://github.com/r-huijts/strava-mcp) | 通用工具 | 465 | MIT | planned | `planned-wave22-multitenant-oauth-foundation-required` |
| 69 | `runekaagaard-mcp-alchemy` | [runekaagaard/mcp-alchemy](https://github.com/runekaagaard/mcp-alchemy) | 数据库 | 414 | MPL-2.0 | blocked | `blocked-superseded-existing-capability` |
| 70 | `safedep-vet` | [safedep/vet](https://github.com/safedep/vet) | 安全分析 | 1096 | Apache-2.0 | ready | `ready-native-anonymous-package-insight-facade` |
| 71 | `stack-chan-stack-chan` | [stack-chan/stack-chan](https://github.com/stack-chan/stack-chan) | 通用工具 | 1646 | Apache-2.0 | blocked | `blocked-physical-device-control` |
| 72 | `tacticlaunch-mcp-linear` | [tacticlaunch/mcp-linear](https://github.com/tacticlaunch/mcp-linear) | 效率与协作 | 146 | MIT | planned | `planned-wave22-multitenant-oauth-foundation-required` |
| 73 | `tiberriver256-mcp-server-azure-devops` | [Tiberriver256/mcp-server-azure-devops](https://github.com/Tiberriver256/mcp-server-azure-devops) | 版本控制 | 382 | MIT | planned | `planned-wave22-multitenant-oauth-foundation-required` |
| 74 | `xquik-dev-x-twitter-scraper` | [Xquik-dev/x-twitter-scraper](https://github.com/Xquik-dev/x-twitter-scraper) | 通讯与协作 | 178 | MIT | blocked | `blocked-social-publishing-or-session-reuse` |
| 75 | `alexei-led-aws-mcp-server` | [alexei-led/cloud-mcp-server](https://github.com/alexei-led/cloud-mcp-server) | 云平台与运维 | 185 | MIT | blocked | `blocked-privileged-infrastructure-write` |
| 76 | `arcadedata-arcadedb` | [ArcadeData/arcadedb](https://github.com/ArcadeData/arcadedb) | 数据库 | 1068 | Apache-2.0 | planned | `planned-read-only-data-facade` |
| 77 | `carterlasalle-mac-messages-mcp` | [carterlasalle/mac_messages_mcp](https://github.com/carterlasalle/mac_messages_mcp) | 通讯与协作 | 313 | MIT | blocked | `blocked-desktop-host-instance-unverified` |
| 78 | `cr7258-elasticsearch-mcp-server` | [cr7258/elasticsearch-mcp-server](https://github.com/cr7258/elasticsearch-mcp-server) | 数据库 | 302 | Apache-2.0 | ready | `ready-isolated-readonly-data-service-facade` |
| 79 | `datalayer-jupyter-mcp-server` | [datalayer/jupyter-mcp-server](https://github.com/datalayer/jupyter-mcp-server) | 数据分析 | 1239 | BSD-3-Clause | blocked | `blocked-arbitrary-command-or-code-execution` |
| 80 | `emiliaprotocol-emilia-protocol` | [emiliaprotocol/emilia-protocol](https://github.com/emiliaprotocol/emilia-protocol) | 安全分析 | 799 | Apache-2.0 | blocked | `blocked-not-an-executable-mcp-server` |
| 81 | `eyalzh-browser-control-mcp` | [eyalzh/browser-control-mcp](https://github.com/eyalzh/browser-control-mcp) | 浏览器与网页 | 314 | MIT | blocked | `blocked-arbitrary-browser-or-url-surface` |
| 82 | `ferdousbhai-investor-agent` | [ferdousbhai/investor-agent](https://github.com/ferdousbhai/investor-agent) | 金融与市场 | 344 | MIT | blocked | `blocked-publishing-or-high-risk-advice` |
| 83 | `jpisnice-shadcn-ui-mcp-server` | [Jpisnice/shadcn-ui-mcp-server](https://github.com/Jpisnice/shadcn-ui-mcp-server) | 开发与代码 | 2923 | MIT | ready | `ready-native-pinned-component-metadata-facade` |
| 84 | `kiliczsh-mcp-mongo-server` | [kiliczsh/mcp-mongo-server](https://github.com/kiliczsh/mcp-mongo-server) | 数据库 | 281 | MIT | blocked | `blocked-superseded-existing-capability` |
| 85 | `lpigeon-ros-mcp-server` | [robotmcp/ros-mcp-server](https://github.com/robotmcp/ros-mcp-server) | 开发与代码 | 1381 | Apache-2.0 | blocked | `blocked-physical-device-control` |
| 86 | `nwiizo-tfmcp` | [nwiizo/tfmcp](https://github.com/nwiizo/tfmcp) | 云平台与运维 | 371 | MIT | blocked | `blocked-privileged-infrastructure-write` |
| 87 | `vectorize-io-vectorize-mcp-server` | [vectorize-io/vectorize-mcp-server](https://github.com/vectorize-io/vectorize-mcp-server) | 搜索与研究 | 110 | MIT | planned | `planned-real-account-readonly-preflight-required` |
| 88 | `antvis-mcp-server-chart` | [antvis/mcp-server-chart](https://github.com/antvis/mcp-server-chart) | 数据分析 | 4307 | MIT | ready | `ready-isolated-deterministic-file-artifact-facade` |
| 89 | `caol64-wenyan-mcp` | [caol64/wenyan-mcp](https://github.com/caol64/wenyan-mcp) | 效率与协作 | 1294 | Apache-2.0 | blocked | `blocked-social-publishing-or-session-reuse` |
| 90 | `dataeval-dingo` | [MigoXLab/dingo](https://github.com/MigoXLab/dingo) | 数据分析 | 736 | Apache-2.0 | ready | `ready-isolated-file-analysis-facade` |
| 91 | `g0t4-mcp-server-commands` | [g0t4/mcp-server-commands](https://github.com/g0t4/mcp-server-commands) | 开发与代码 | 232 | MIT | blocked | `blocked-arbitrary-command-or-code-execution` |
| 92 | `keboola-keboola-mcp-server` | [keboola/mcp-server](https://github.com/keboola/mcp-server) | 数据分析 | 84 | MIT | planned | `planned-real-account-readonly-preflight-required` |
| 93 | `line-line-bot-mcp-server` | [line/line-bot-mcp-server](https://github.com/line/line-bot-mcp-server) | 通讯与协作 | 756 | Apache-2.0 | blocked | `blocked-broad-account-or-message-write` |
| 94 | `ozgurcd-gograph` | [ozgurcd/gograph](https://github.com/ozgurcd/gograph) | 开发与代码 | 209 | MIT | ready | `ready-isolated-code-index-facade` |
| 95 | `patdolitse-piia-engram` | [Patdolitse/piia-engram](https://github.com/Patdolitse/piia-engram) | 知识与记忆 | 169 | AGPL-3.0 | planned | `planned-wave21-stateful-foundation-required` |
| 96 | `taisly-agent` | [taisly/agent](https://github.com/taisly/agent) | 社交与内容 | 264 | MIT | blocked | `blocked-social-publishing-or-session-reuse` |
| 97 | `zinja-coder-jadx-ai-mcp` | [zinja-coder/jadx-ai-mcp](https://github.com/zinja-coder/jadx-ai-mcp) | 安全分析 | 2621 | Apache-2.0 | blocked | `blocked-desktop-host-instance-unverified` |
| 98 | `alexander-zuev-supabase-mcp-server` | [alexander-zuev/supabase-mcp-server](https://github.com/alexander-zuev/supabase-mcp-server) | 数据库 | 830 | Apache-2.0 | blocked | `blocked-superseded-unbounded-third-party` |
| 99 | `bx33661-wireshark-mcp` | [bx33661/Wireshark-MCP](https://github.com/bx33661/Wireshark-MCP) | 安全分析 | 192 | MIT | blocked | `blocked-arbitrary-host-or-target-surface` |
| 100 | `bytedance-ui-tars-desktop-browser` | [bytedance/UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | 浏览器与网页 | 38534 | Apache-2.0 | blocked | `blocked-arbitrary-browser-or-url-surface` |

## 执行边界

批次 14—19A 的二十二项只读、公共研究、确定性文件与数据服务能力均锁定上游身份、出口、Schema 与输出上限；其余 78 项没有新增默认执行配置，18 项保留后续受控 facade 规划，60 项因重复、漂移、安全、身份、宿主或代码索引实现收敛而阻断。
