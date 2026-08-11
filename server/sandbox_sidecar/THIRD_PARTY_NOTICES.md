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
(MIT), and python-pptx 1.0.2 (MIT). The Office libraries are used only after
the sidecar independently validates the complete OOXML package. Transitive
package metadata and Debian license texts remain available in the image's
installed package and `/usr/share/doc` directories.

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
