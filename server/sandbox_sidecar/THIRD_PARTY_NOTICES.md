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
matplotlib 3.10.7 (PSF-based), and MarkItDown 0.1.7 (MIT). Transitive package
metadata and Debian license texts remain available in the image's installed
package and `/usr/share/doc` directories.

The `modelmirror-mcp-token:wave4-v1` runtime bundles the following pinned MCP
packages and their locked transitive npm dependencies. Runtime installation and
update checks are disabled; package license metadata is retained under
`/opt/modelmirror/node_modules`:

- AgentQL MCP 1.0.1, Brave Search MCP 0.6.2, Exa MCP 3.4.0, Figma Developer
  MCP 0.13.2, Firecrawl MCP 3.23.4, Google Maps MCP 0.6.2, Graphlit MCP
  1.0.20260112001, Perplexity MCP 1.2.0, Shodan MCP 1.0.22, Tavily MCP
  0.2.22, and VirusTotal MCP 1.0.25: MIT License.

The Axiom v0.05, Grafana MCP v1.0.0, Kagi MCP commit `0d62ed3`, and Pinecone
Assistant MCP v0.1.0 contracts are implemented independently as minimal
read-only compatibility adapters. No upstream Axiom, Grafana, Kagi, or
Pinecone server source is copied into the image. Grafana's reviewed upstream
server is Apache-2.0; the other reviewed contracts are MIT.

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
