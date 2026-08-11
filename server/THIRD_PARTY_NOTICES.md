# Third-Party Notices

ModelMirror uses third-party libraries under their respective licenses.

## Agency Orchestrator core

ModelMirror selectively vendors the Apache-2.0 orchestration core from
`jnMetaCode/agency-orchestrator` at commit
`3b7c43042325a9091393de6ecfa7e9936b0c7932`. The source, license, exact Blob
SHA map and ModelMirror modification log are stored under
`server/vendor/agency-orchestrator/` in the source repository. The service
image contains only the compiled Worker closure and its production dependency;
it excludes upstream Provider Connectors, websites, Studio, Electron, role
libraries, creative assets and third-party CLI integrations.

Project: https://github.com/jnMetaCode/agency-orchestrator

## PenguinHarness built-in Skills

ModelMirror vendors the 16 upstream Skill directories from
`Prism-Shadow/penguin-harness` under the Apache License 2.0. They are stored
at `/app/skills/builtin` in the server image and are used only as versioned
Agent State instruction snapshots. The complete upstream license is included
at `/app/skills/builtin/PENGUINHARNESS_LICENSE`.

Project: https://github.com/Prism-Shadow/penguin-harness

ModelMirror does not run or bundle the PenguinHarness application, CLI, SDK,
logo, or release binaries. Four Skill instruction files (`agent-creation`,
`skill-porting`, `software-engineering`, and `web-design`) carry prominent
inline notices describing ModelMirror modifications. The built-in manifest
records source paths, Apache-2.0 attribution, modification flags, capability
status, and SHA-256 content digests. Reference-only Skills are retained for
review but are not injected into the runtime in this phase.

## pypdfium2 and PDFium

- `pypdfium2` is distributed under Apache-2.0 OR BSD-3-Clause.
- PDFium is distributed under a BSD-style license and includes additional
  notices for bundled third-party components.
- Binary distributions must retain the license files shipped with the
  `pypdfium2` wheel.

Project: https://github.com/pypdfium2-team/pypdfium2

## Pillow

Pillow is distributed under the HPND license.

Project: https://python-pillow.org/

These libraries are used for local image validation and PDF page rendering.
No source code from Xpert or Dify is copied into ModelMirror; those projects
are used only as behavioral and domain-model references where permitted.

## jsonschema

`jsonschema` is distributed under the MIT License. ModelMirror uses it to
validate Draft 2020-12 schemas for Agent structured output. No upstream
source code is copied into this repository.

Project: https://github.com/python-jsonschema/jsonschema

## Sandbox sidecar

The isolated Sandbox sidecar is implemented independently by ModelMirror and
uses official CPython and Node.js runtime images plus Debian packages for Git
and ripgrep. Its complete runtime notice is included at
`/usr/share/doc/modelmirror-sandbox/THIRD_PARTY_NOTICES.md` inside the sidecar
image. No Xpert or Dify source code is copied into the Sandbox implementation.

## OpenCode Coding Runtime

The optional `coding` profile installs `opencode-ai` 1.18.9 under the MIT
License and runs it as a separate ACP command-line process inside the isolated
Coding Runtime container.

Project: https://github.com/anomalyco/opencode

The npm package is pinned to version 1.18.9 and verified during image build
against its published SHA-512 integrity value. OpenCode and its dependencies
retain their own license notices under the image's global Node.js modules
directory. ModelMirror communicates with OpenCode only through ACP and does not
copy OpenCode source code into the application.

The Coding Runtime also installs Debian's `ripgrep` 14.1.1 package as the
search backend required by OpenCode's built-in glob and grep tools. ripgrep is
distributed under the MIT License or the Unlicense.

Project: https://github.com/BurntSushi/ripgrep

## Coding Verifier, Applier, and Committer

The optional `coding-verify` profile uses the project's locked CPython and
Node.js dependency sets to run fixed backend tests and frontend builds in a
separate offline container. It does not install OpenCode and does not accept
runtime package installation.

The Verifier image is built from the official Python 3.12 and Node.js 22 slim
images. The optional `coding-apply` and `coding-commit` images use the official
Python 3.12 slim image. These offline images install Debian's Git package only
for fixed internal patch or Git plumbing operations; the Committer performs no
remote operation. Python packages retain their metadata under the image's
Python site-packages directory; npm packages in the Verifier retain their
license and package metadata under `/opt/modelmirror-client/node_modules`.
Their upstream licenses remain unchanged.

## Coding GitHub Publisher

The optional `coding-publish` profile uses the official Python 3.12 slim image,
Debian's Git and CA certificates, `httpx` 0.28.1, and `cryptography` 45.0.7.
`httpx` is distributed under the BSD 3-Clause License. `cryptography` is
distributed under either the Apache License 2.0 or the BSD 3-Clause License.
Git is distributed under GPL-2.0; the image retains Debian's package notices.

Projects:

- https://github.com/encode/httpx
- https://github.com/pyca/cryptography
- https://git-scm.com/

The Publisher and its allowlist egress proxy are independently implemented by
ModelMirror. They use GitHub's public REST and GraphQL APIs with a deployment
owned GitHub App; no GitHub source code, private key, JWT, or installation token
is copied into the repository or image.

## Browser sidecar

The isolated Browser sidecar uses Playwright 1.58.2 under the Apache License
2.0 and `ipaddr.js` under the MIT License. Chromium and bundled components are
distributed with the BSD-style and component-specific notices retained by the
official Playwright browser image.

The sidecar includes its runtime notice at
`/usr/share/doc/modelmirror-browser/THIRD_PARTY_NOTICES.md`. ModelMirror's
Browser implementation is independently written; no Xpert AGPL source code is
copied into this component.

## Office.js and NGINX

The optional Office Task Pane loads Office.js from Microsoft's official CDN.
The OfficeDev/office-js project is distributed under the MIT License; its
source is not copied or bundled into this repository.

Project: https://github.com/OfficeDev/office-js

The optional `office-host` profile uses the official NGINX image to serve the
Task Pane and proxy the local HTTPS/WSS endpoints. NGINX is distributed under
the 2-clause BSD License. The Office automation implementation is independently
written; Xpert AGPL code is used only as a behavioral reference.

## Data X analytics dependencies

DuckDB is distributed under the MIT License and provides the isolated local
analytics engine used by Data X projects.

Project: https://github.com/duckdb/duckdb

openpyxl is distributed under the MIT License and is used to import XLSX source
snapshots. Recharts is distributed under the MIT License and renders the fixed
KPI, table, line, and bar result views in the React client.

Projects:

- https://foss.heptapod.net/openpyxl/openpyxl
- https://github.com/recharts/recharts

No Xpert Data X source code is copied into ModelMirror. Its public documentation
is used as a domain reference while the implementation remains independent.

## API Toolset document parsers

PyYAML is distributed under the MIT License and parses trusted-management-plane
OpenAPI YAML documents.

Project: https://github.com/yaml/pyyaml

defusedxml is distributed under the Python Software Foundation License and
parses OData v4 CSDL metadata with XML entity and expansion protections.

Project: https://github.com/tiran/defusedxml

No Xpert AGPL implementation is copied into the API Toolset runtime. Xpert UI
and public documentation are used only as behavioral and domain-model
references.

## Anthropic skill-creator authoring guidance

ModelMirror includes a modified creation-stage authoring playbook derived from
`anthropics/skills`, path `skills/skill-creator/`.

- Copyright: Copyright 2026 Anthropic, PBC
- License: Apache License 2.0
- Upstream: <https://github.com/anthropics/skills/tree/main/skills/skill-creator>
- Modified file: `/app/skills/creator_reference/authoring-playbook-v1.md`
- License copy:
  `/app/skills/creator_reference/LICENSE-ANTHROPIC-SKILL-CREATOR.txt`

The playbook was rewritten for ModelMirror's typed proposal, UTF-8 package,
interactive resource-planning and build flow, trusted requirement coverage,
and separate evaluation and installation gates. It excludes the
upstream evaluator, graders, result viewer, description optimizer, packaging
scripts and Claude-specific execution paths. Anthropic does not endorse
ModelMirror; this notice uses the project name only to identify provenance.

## PenguinHarness execution core (R3R-1)

The server image contains a byte-identical PenguinHarness Core/Skills vendor at
revision `047505dccc0cc16ad92be11011347d635f33ceb0`.

- Project: <https://github.com/Prism-Shadow/penguin-harness>
- License: Apache License 2.0
- Source and upstream notices:
  `/app/agent_upstream/vendor/penguin_harness/`
- Blob manifest and supply-chain reports:
  `/app/agent_upstream/provenance/`

The server builds this vendor with pinned Node 24.19.0 and pnpm 11.18.0. The
dedicated runtime license is stored at
`/opt/modelmirror-upstream-node/LICENSE`. ModelMirror-owned IPC, process
supervision, model gateway, filesystem policy, API, and UI adapters are outside
the immutable vendor directory.

This image does not include the PenguinHarness Server/Hono API, database,
Vault, cost center, desktop application, CLI, installer, MinGit, release
tooling, or Penguin branding.
