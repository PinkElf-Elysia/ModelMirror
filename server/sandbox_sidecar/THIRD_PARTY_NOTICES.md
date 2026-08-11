# ModelMirror Sandbox Third-Party Notices

The Sandbox sidecar is an independent ModelMirror implementation. No Xpert
or Dify source code is copied into this image.

Runtime components are distributed under their respective licenses:

- CPython: Python Software Foundation License.
- Node.js: MIT License; the official Node container also carries notices for
  its bundled dependencies.
- npm and npx: Artistic License 2.0.
- ripgrep: The Unlicense OR MIT License.
- Git: GNU General Public License version 2. Git is invoked only as a separate
  command-line program for local workspace content; ModelMirror does not link
  against or copy Git source code.
- Debian base packages retain their package license files under
  `/usr/share/doc`.

The Landlock integration calls the Linux kernel userspace ABI through Python
`ctypes`; it does not bundle a third-party Landlock library.

Wave-2 compatibility contracts were independently implemented after reviewing
the following open-source MCP servers. Their source packages are not installed
or dynamically downloaded by the sidecar:

- MCP Fetch Server 0.6.3 (`modelcontextprotocol/servers`): MIT License.
- QuickChart MCP Server 1.0.6 (`GongRzhe/Quickchart-MCP-Server`): MIT License.
- GeoWire MCP 0.6.2 (`geowire/geowire`): Apache License 2.0.
- OpenBnB Airbnb MCP 0.3.0 (`openbnb-org/mcp-server-airbnb`): MIT License.
  The catalog adapter is currently blocked because its public page contract
  failed the schema-drift smoke gate.

Public geocoding and routing results may be subject to provider-specific terms
and attribution requirements. The adapter preserves OpenStreetMap attribution
and rate-limits public Nominatim access.

The same public sidecar implements three independently reviewed Wave 16A
compatibility contracts. No source package from these upstream MCP servers is
installed or copied into the image, and no package is downloaded at runtime:

- DuckDuckGo MCP Server v0.6.1
  (`nickclyde/duckduckgo-mcp-server`, commit
  `ad2e681bfb4461c969d3032b47ac5b3cd513f0a9`): MIT License. Only bounded
  strict-SafeSearch result metadata from `html.duckduckgo.com` is represented;
  arbitrary URL content fetching is absent.
- shadcn/ui MCP Server v2.0.0
  (`Jpisnice/shadcn-ui-mcp-server`, commit
  `d750f1645bb0fe10c6fbf5e246bc3b12d3807c05`): MIT License. Only component
  names and Git metadata from `shadcn-ui/ui` commit
  `d14b6e69a91f0fc99e31a7adb26a48d661df9911` are represented; source,
  demos, blocks, themes, tokens and project writes are absent.
- Docker Hub MCP v0.18.0 (`docker/hub-mcp`, tag `dockerhub-mcp/v0.18.0`,
  commit `98cf1b9cbec64316ea2b465462468a2d2204a406`): Apache-2.0. Only anonymous
  public repository search, repository metadata and tag metadata from
  `hub.docker.com` are represented; accounts, namespaces, DHI, repository
  creation/update, image pulls and execution are absent.

The public sidecar also implements two independently reviewed Wave 16B
compatibility contracts. Their source packages and binaries are not installed,
copied or executed by the image:

- BioMCP v0.8.25 (`genomoncology/biomcp`, commit
  `b5337826dbf06db6d6409f36ead7a4d6a70c710e`): MIT License. Only the reviewed
  `search` and `get` shapes are represented over fixed Europe PMC,
  ClinicalTrials.gov and MyVariant.info public metadata endpoints. The raw
  BioMCP query escape hatch, study files and diagnostic upload are absent.
- SafeDep Vet v1.18.1 (`safedep/vet`, commit
  `67abab1b0ec915713edb50e5e5b36687fd4cd86a`): Apache-2.0. Only six reviewed
  package-insight operations over the anonymous SafeDep community service and
  public npm/PyPI registries are represented. The Vet binary is not bundled;
  package download, extraction, execution, scan/upload, SQL, authenticated
  tenants and configurable registries are absent.

The public sidecar additionally implements three independently reviewed Wave
17A compatibility contracts. Their source packages and binaries are not
installed, copied or executed by the image:

- open-webSearch v2.1.9 (`Aas-ee/open-webSearch`, release commit
  `84695b392ca03ffc68fbd406f1d7937b7151e4b6`): Apache-2.0. Only the fixed
  request-mode `cn.bing.com` RSS and DuckDuckGo strict-search engines are represented;
  arbitrary URL fetching, Playwright, proxy configuration and insecure TLS are
  absent.
- Idea Reality MCP v0.5.0 (`mnemox-ai/idea-reality-mcp`, commit
  `755e1859c1f7d1d017c67f615c67ec595c8edb66`): MIT. Only deterministic
  public-index similarity research over GitHub, Hacker News, npm and PyPI is
  represented. Product Hunt credentials, LLM calls and uploads are absent.
- GitMCP reviewed commit `c487a29895dcfcb5b672247e646426a56e2051c1`
  (`idosal/git-mcp`): Apache-2.0. Only bounded README, documentation-path and
  repository-path searches for canonical GitHub owner/repository slugs are
  represented. The dynamic GitMCP endpoint, arbitrary URL fetch, clone, token
  and repository writes are absent.

Wave-3 file adapters are independently implemented compatibility contracts.
The sidecar does not install or copy the upstream Basic Memory, Excel MCP,
Git MCP, MarkItDown MCP, or Manim MCP server packages:

- Basic Memory v0.22.1 (`basicmachines-co/basic-memory`) was reviewed under
  AGPL-3.0. No Basic Memory source or binary is included; ModelMirror only
  provides an independent local Markdown contract and records this separation
  explicitly for AGPL compliance review.
- Excel MCP 1.0.4 (`yzfly/mcp-excel-server`) was reviewed under MIT as an
  upstream tool contract. Its server package is not bundled.
- Git MCP 0.6.2 (`modelcontextprotocol/servers`, server-git) was reviewed under
  MIT. Its server package is not bundled; the sidecar invokes the separately
  installed Git CLI through a fixed read-only wrapper.
- MarkItDown MCP v0.1.7 (`microsoft/markitdown`) was reviewed under MIT. The MCP
  server package is not bundled; the image installs the official MarkItDown
  Python library 0.1.7 and calls only its local-file conversion API.
- Manim MCP remains blocked and no Manim package or scene runtime is included.

The `modelmirror-mcp-files:wave3-v1` runtime also includes these pinned direct
dependencies under their upstream licenses: MCP Python SDK 1.27.2 (MIT),
pandas 2.3.3 (BSD-3-Clause), openpyxl 3.1.5 (MIT), xlrd 2.0.2 (BSD),
matplotlib 3.10.7 (PSF-based), MarkItDown 0.1.7 (MIT), python-docx 1.2.0
(MIT), python-pptx 1.0.2 (MIT), and ReportLab 5.0.0 (BSD-3-Clause). The Office
libraries and ReportLab are used only by fixed local parsing or rendering
contracts; the renderer cannot load URLs, run macros, or select output paths.
The Office libraries are used only after
the sidecar independently validates the complete OOXML package. Transitive
package metadata and Debian license texts remain available in the image's
installed package and `/usr/share/doc` directories.

Wave-18A adds three deterministic artifact compatibility contracts to the same
file image. They are enabled only through the exact production allowlist after
acceptance. The reviewed MCP server packages are not installed or executed:

- Markdownify MCP v1.1.0 (`zcaceres/markdownify-mcp`), commit
  `024f97cea9a94cd842c445eea4503c442c79bd71`: MIT License. Only four selected
  local file-to-Markdown tool identities are represented using the already
  pinned MarkItDown 0.1.7 library; all URL, Git, image and audio tools are absent.
- MCP Pandoc v0.11.0 (`vivekVells/mcp-pandoc`), commit
  `120dc78f1fe243b72631029ee0f33ab77034ea34`: MIT License. Only a path-free,
  filter-free subset of `convert-contents` is represented.
- AntV MCP Server Chart 0.9.10 (`antvis/mcp-server-chart`), commit
  `2ed4a03b12e2fd82f6d2d0ece337f6ddb12966b9`: MIT License. The official server
  calls a remote AntV Studio endpoint and is not included. ModelMirror provides
  an independent, explicitly labelled local compatibility facade for line, bar
  and pie PNG artifacts using the pinned Matplotlib dependency.

The file image now includes the official Pandoc 3.10.1 Linux binary under
GPL-2.0. The upstream amd64 and arm64 release archives are verified by their
published SHA-256 digests during the image build. The exact `COPYING.md` from
tag 3.10.1 is installed at `/usr/share/doc/pandoc/COPYING.md`; corresponding
source is available from `https://github.com/jgm/pandoc/tree/3.10.1`.

Wave-18B adds three file-analysis compatibility contracts to the same image.
No upstream MCP server package or general-purpose execution runtime is copied
or executed. After isolated runtime and user acceptance, only the three exact
IDs are present in the production allowlist:

- llm-context 0.6.4 (`cyberchitta/llm-context.py`), commit
  `6de16c22458d0e145ac6c440ef732849e7ae3d9f`: Apache-2.0. Only sealed-workspace
  file preview and deterministic outline generation are represented.
- Excel MCP Server v0.1.8 (`haris-musa/excel-mcp-server`), commit
  `f51340ecd5778952405044b203d3a2d4c8a46833`: MIT. Only bounded metadata/range
  reads and formula-free output-copy generation are represented using the
  already pinned openpyxl dependency.
- Dingo v2.5.0 (`MigoXLab/dingo`), commit
  `c3674f903f88043aa24bf99d21d557fa966ab23f`: Apache-2.0. Only three reviewed
  local rules are represented; LLM, Agent, cloud, SQL and registry inputs are
  absent.

The `modelmirror-mcp-token:wave4-v1` runtime bundles the following pinned MCP
packages and their locked transitive npm dependencies. Runtime installation and
update checks are disabled; package license metadata is retained under
`/opt/modelmirror/node_modules` or the isolated `/opt/modelmirror/brave/node_modules`:

- AgentQL MCP 1.0.1, Brave Search MCP 0.6.2, Exa MCP 3.4.0, Figma Developer
  MCP 0.13.2, Firecrawl MCP 3.23.4, Google Maps MCP 0.6.2, Graphlit MCP
  1.0.20260112001, Perplexity MCP 1.2.0, Shodan MCP 1.0.22, Tavily MCP
  0.2.22, and VirusTotal MCP 1.0.25: MIT License.

- Official Brave Search MCP Server 2.1.0
  (`brave/brave-search-mcp-server`, release commit
  `76106c83f9d57319478b540374ba261061d26c3e`): MIT License. Its independent
  npm lock is stored under `brave_runtime/`; only `brave_web_search` and
  `brave_local_search` are enabled, while all other upstream tools are hidden
  and rejected by the gateway.

The Axiom v0.05, Grafana MCP v1.0.0, Kagi MCP commit `0d62ed3`, and Pinecone
Assistant MCP v0.1.0 contracts are implemented independently as minimal
read-only compatibility adapters. No upstream Axiom, Grafana, Kagi, or
Pinecone server source is copied into the image. Grafana's reviewed upstream
server is Apache-2.0; the other reviewed contracts are MIT.

The same token image also implements two independently reviewed Wave 14
compatibility contracts. No source or package from either upstream MCP server
is copied into the image:

- Official Kagi MCP Server v1.0.2 (`kagisearch/kagimcp`, commit
  `55b38d20c67f1406f2c284af776de395297a75cc`): MIT License. Only
  `kagi_search_fetch` and `kagi_extract` are represented; the API host,
  request fields, output limits, redirects and retries are fixed locally.
- arXiv MCP Server v0.6.2 (`blazickjp/arxiv-mcp-server`, commit
  `8f79e8853c1104630a647889abb430fab539130e`): Apache-2.0. Only
  `search_papers` and `get_abstract` metadata operations are represented;
  download, local storage, alerts, semantic indexing and citation export are
  absent.

The token image additionally implements two independently reviewed Wave 15
compatibility contracts. No source or package from either upstream MCP server
is copied into the image:

- Official Search1API MCP Server v0.5.3
  (`superagents-lab/search1api-mcp`, commit
  `3e5e86c3c1d138c61dd6508caa764c52564628b3`): MIT License. Only `search`,
  `news`, and `trending` are represented; crawling, sitemaps, screenshots,
  extraction, OAuth and configurable endpoints are absent.
- Official Live Tennis API MCP Server v1.4.0
  (`livetennisapi/livetennisapi-mcp`, commit
  `46a80cbb25d58a293fcff7a866472043cc6e791d`): MIT License. Only the FREE
  live/upcoming scores, player, fixture and tournament directory surfaces are
  represented; odds, markets, predictions, model fields, history, statistics,
  WebSockets and usage probing are absent.

Snyk MCP 1.15.2 was reviewed under Apache-2.0 but is not included. It remains
blocked because local project scanning can invoke host-language build tools and
therefore requires the later one-shot code-execution isolation boundary.

The Token sidecar also contains four default-disabled, independently implemented
read-only compatibility contracts under Wave 17B. No upstream server package is
copied or executed, and the four IDs are not in the production allowlist until a
real account read-only preflight is accepted:

- `cablate/mcp-google-map` v0.0.53, commit
  `6c34d268126a31390f2e236d888b0a00fed59f11` (MIT). Only Places search and
  metadata details are represented.
- `vectorize-io/vectorize-mcp-server` 0.4.3, commit
  `bea6442bf77165ff26fc66fb4107b741811ae1a9`. The tagged repository `LICENSE`
  is MIT while `package.json` declares ISC; this metadata conflict must be
  resolved before promotion. Only retrieval from an existing configured
  pipeline is represented; extraction/upload and deep research are absent.
- `comet-ml/opik-mcp` 0.2.15, commit
  `8ce6f3375068768adca3df2f804f12f0213dbb65` (Apache-2.0). Only the universal
  `list` and `read` identity is represented; writes, schema, Ollie and experiment
  execution are absent.
- `keboola/mcp-server` v1.75.2, commit
  `653118bc42fab00fe0268b6feaf4c4ad032dbf7c` (MIT). Only project, bucket and
  table metadata on the fixed `connection.keboola.com` stack are represented.

The `modelmirror-mcp-database:wave5-v1` image implements independent, fixed
read-only compatibility contracts after reviewing these upstream projects. It
does not install their MCP server packages and never downloads runtime code:

- DBHub 1.2.0 (`bytebase/dbhub`): MIT License.
- MongoDB MCP Server 2.0.0 (`mongodb-js/mongodb-mcp-server`): Apache-2.0.
- ClickHouse MCP 0.4.1 (`ClickHouse/mcp-clickhouse`): Apache-2.0.
- Redis MCP Server 0.5.1 (`redis/mcp-redis`): MIT License.
- MotherDuck MCP Server 1.0.7 (`motherduckdb/mcp-server-motherduck`): MIT
  License. Only an isolated local DuckDB-compatible subset is implemented.
- Supabase MCP Server 0.9.0 (`supabase-community/supabase-mcp`): Apache-2.0.
  Only project-scoped local stdio with a PAT is represented; OAuth is absent.

The same database image contains three default-disabled Wave 19A native REST
facades. No upstream MCP package or source is copied or executed. Each facade
uses only provider-documented read APIs, one fixed host and one project-bound
resource where applicable:

- Prometheus MCP Server v1.6.2 (`pab1it0/prometheus-mcp-server`, commit
  `c41d06924ce436c2b63edd20671f77f5b7564bb0`): MIT License. Only instant/range
  PromQL, metric names/metadata and active targets are represented.
- Qdrant MCP Server v0.8.1 (`qdrant/mcp-server-qdrant`, commit
  `860ab93a96ca9f5e6cf6fe47e2f5b75d36eaac69`): Apache-2.0. The upstream
  `qdrant-store` tool is absent; the facade binds one collection and exposes
  collection metadata, bounded scroll and bounded vector query only.
- Elasticsearch MCP Server v2.1.2 (`cr7258/elasticsearch-mcp-server`, commit
  `56ab520f863cd36fcd00e984ebc384f6cdf8279c`): Apache-2.0. Write/delete,
  administrative and general API tools are absent; the facade binds one index
  and search field and requires a native read-only account.

The isolated acceptance services were Prometheus 3.12.0, Qdrant 1.18.2 and
Elasticsearch 8.17.2. Those provider images are not part of the ModelMirror
database sidecar image.

The same database image also contains three default-disabled Wave 19B native
REST facades. No upstream MCP package or source is copied or executed:

- MCP Server for Milvus 0.1.1 (`zilliztech/mcp-server-milvus`, commit
  `a7e624f3057a0d739528bca3ed92504943224ceb`): Apache-2.0. The facade binds
  one database, collection, vector field and output-field list; insert,
  delete, upsert, collection management and filter expressions are absent.
- Neo4j MCP Cypher `mcp-neo4j-cypher-v0.6.0` (`neo4j-contrib/mcp-neo4j`,
  commit `dbc01ba78f171851f2d57dcd125b028c29912fd1`): MIT. The facade uses the
  provider Query API with a native reader account; writes, procedures,
  extensions and administrative tools are absent.
- ArcadeDB 26.8.1 (`ArcadeData/arcadedb`, commit
  `87bdc67f1f0331fa2d07e932a550064c118eae70`): Apache-2.0. The facade uses
  only the HTTP query endpoint with a native readonly account; commands,
  imports, scripts, administration and the upstream general MCP endpoint are
  absent.

The isolated acceptance services were Milvus 2.5.21, Neo4j Enterprise
5.26.12 and ArcadeDB 26.8.1. Those provider images and Neo4j Enterprise are
test-only dependencies and are not copied into or distributed with the
ModelMirror database sidecar image.

The `modelmirror-mcp-files` image also bundles GoGraph v1.5.6
(`ozgurcd/gograph`, commit
`aa4d6d549e64f35c492664263630ba1350c66920`), licensed under MIT. The exact
release archives are pinned as follows:

- linux-amd64 archive SHA-256:
  `1ef375a88cc8825ca7879b1170720352702e59723d1e3b06d33101a50a6f7030`;
- linux-arm64 archive SHA-256:
  `c8b6d8a42326264858f14c7819200f47d00d0fcd58520b6c6d1e1b16b022a6b5`.

The image includes the official Go 1.26.5 toolchain (BSD-3-Clause) from the
multi-platform image index
`sha256:53eeac89074db483fdf0ab3be1df32bf6e47562263d2d0d6baa7f26acb4957dd`.
The toolchain is used only by GoGraph to resolve the sealed repository's local
build context. Network module and toolchain resolution are disabled.

The GoGraph process is started without `--persist-refresh`; its initial graph
and later refreshes remain in memory. ModelMirror exposes only six fixed Go
index and read tools. Arbitrary paths, Git baselines, boundary configuration,
session telemetry, wiki generation, `go doc`, commands and environment maps
are not exposed. GoGraph and Go license files remain under `/usr/share/doc`.

Codebase Memory MCP v0.10.1 (`DeusData/codebase-memory-mcp`, MIT) and
CodeGraphContext v0.5.7 (`CodeGraphContext/CodeGraphContext`, MIT) were
reviewed for Wave 20 but are not bundled or executed. Codebase Memory's
mandatory process-coordination path is incompatible with the sealed Landlock
workspace boundary; CodeGraphContext's multi-database and broad control plane
is not retained by the single bounded GoGraph facade.

The archived PostgreSQL and SQLite reference servers are not bundled. Cognee,
Graphiti and Hindsight are also absent because their stateful memory, model and
storage requirements remain blocked for a later isolation batch.

Pinned direct Python dependencies retain their installed package metadata and
license files: MCP Python SDK (MIT), certifi (MPL-2.0), clickhouse-connect
(Apache-2.0), DuckDB (MIT), httpx (BSD-3-Clause), psycopg (LGPL-3.0), PyMongo
(Apache-2.0), PyMySQL (MIT), redis-py (MIT), and SQLGlot (MIT). Debian package
notices remain under `/usr/share/doc`.

The `modelmirror-mcp-saas:wave6-v1` image implements independent, fixed API
contracts after reviewing the following upstream MCP servers.  It does not
install or copy their server packages and cannot accept provider URLs, command
lines or arbitrary HTTP headers from clients:

- Airtable MCP Server v1.14.0 (`domdomegg/airtable-mcp-server`): MIT License.
- Asana MCP Server v1.6.0 (`roychri/mcp-server-asana`): MIT License.
- Archived GitLab reference server package 0.6.2
  (`modelcontextprotocol/servers-archived`): MIT License.  The adapter is an
  independently implemented, project-scoped GitLab.com REST contract.
- Notion MCP Server v2.5.0 (`makenotion/notion-mcp-server`): MIT License.

The Chinese commerce bundle and archived Mem0 local MCP server are not present
in the image.  Their OAuth/account-lifecycle and remotely versioned contract
requirements remain blocked.  The SaaS image's only direct Python runtime
dependency beyond CPython is the pinned MCP Python SDK 1.27.2 (MIT).

The `modelmirror-mcp-browser:wave7-v1` image runs the following real, pinned
upstream MCP packages behind ModelMirror's fixed Unix-socket policy gateway.
Neither package is downloaded or updated at runtime:

- Chrome DevTools MCP 1.6.0
  (`https://github.com/ChromeDevTools/chrome-devtools-mcp`): Apache-2.0.
- Playwright MCP 0.0.79
  (`https://github.com/microsoft/playwright-mcp`): Apache-2.0.

Playwright MCP 0.0.79 pins Playwright and Playwright Core
1.63.0-alpha-2026-08-05 (Apache-2.0). Its locked Chromium revision 1237
(Chrome for Testing 152.0.7977.8) is installed only while the image is built.
Chrome DevTools MCP 1.6.0 bundles Puppeteer Core 25.3.0 and is paired with its
locked Chrome for Testing 150.0.7871.24. The two adapters use separate fixed
browser paths; neither can select the other adapter's browser or download a
replacement at runtime. Chromium and Chrome for Testing retain their upstream
BSD-style and component-specific license notices. The build-only downloader is
`@puppeteer/browsers` 3.0.6 (Apache-2.0). The npm integrity values and complete
direct dependency graph used by `npm ci` are recorded in
`browser_requirements.lock`; installed package license/source metadata remains
under `/opt/browser-upstream/node_modules`.

The browser service also vendors the Moby seccomp default profile from
`moby/profiles` tag `seccomp/v0.2.3` (Apache-2.0). The vendored profile
retains the upstream default-deny policy and adds only `clone`, `setns`,
`unshare`, and `chroot`. The first three syscalls are required by the non-root
Chromium user-namespace sandbox; `chroot` is used only after Chromium has
entered its self-created user namespace to contract the process into an empty
root. The outer container has no `CAP_SYS_CHROOT` or any other capability, and
no other seccomp rule is relaxed. The browser-specific copy also removes the
modern-kernel legacy unconditional allow for `ptrace`, `process_vm_readv`, and
`process_vm_writev` so same-UID processes cannot inspect one another; only the
upstream `CAP_SYS_PTRACE`-conditioned rule remains, and `cap_drop: ALL` keeps it
inactive. The original source is `seccomp/default.json` in that tagged release.

Puppeteer MCP and Selenium MCP are not installed or executed in Wave 7. Their
catalog entries remain blocked until an independently verified sandbox and
upstream schema contract are available.

The `modelmirror-mcp-registry:wave9-v1` image implements an independent,
credential-free, read-only compatibility subset after reviewing HashiCorp
Terraform MCP Server v1.2.0 (`hashicorp/terraform-mcp-server`), licensed under
MPL-2.0. No HashiCorp server source or binary is copied into the image. The
runtime exposes six public Terraform Registry discovery/documentation tools
and cannot access HCP Terraform, Terraform Enterprise, private registries,
local Terraform files, or infrastructure mutation tools. Its pinned direct
Python runtime dependencies are MCP Python SDK 1.27.2 (MIT) and tzdata 2026.3
(public-domain/IANA database notices retained in installed package metadata).
