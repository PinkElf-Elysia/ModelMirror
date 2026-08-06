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
