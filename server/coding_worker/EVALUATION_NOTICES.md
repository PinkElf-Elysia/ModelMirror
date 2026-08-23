# V20 Standard Driver Evaluation Notices

These components are available only in the separately built, disabled-by-default
Coding Worker evaluation images. They are not installed into the production
Server requirements and are never registered in the production route catalog.

## Agent Client Protocol Python SDK and schema

- Project: `agentclientprotocol/python-sdk`
- Package: `agent-client-protocol` 0.12.0
- License: Apache-2.0
- Wheel SHA-256: `233626748034896214de118f5cf5a319484ad2186705fd595219afee92237ccc`
- Schema: ACP `schema-v1.19.0` at upstream commit
  `a213df5240048f96d2b23f644984bb20c188a234`
- Schema SHA-256: `998c6427fa78bf6cd39f442bf164c6172234ebdf1c04298af57c40fa716ce267`

ModelMirror includes the generated JSON Schema as a fixed conformance fixture.
The ModelMirror ACP adapter is independently written and maps only the frozen
stable subset. The official wheel is installed unchanged in the evaluation
image; its source is not vendored or modified. The adapter does not expose a
host filesystem, terminal, arbitrary MCP server, executable, working directory,
or environment variable to a task.

## OpenAI Codex App Server

- Project: `openai/codex`
- Package: `@openai/codex` 0.149.0
- License: Apache-2.0
- npm integrity: `sha512-i4dryj2Y1j+00Mb5n+0n71EYnTK9/KDc2cdFo/dXD0d1oTog2bhUssKDEIOnKmnEf51P0Z/HJTWvTKw/UHyOvQ==`
- Linux x64 runtime integrity: `sha512-uZXaN9JPxu0/jjnqqJeTd4kRYPnjVZK3MiVndfG1mHhEaoDKL7ScWHfPqvAEOjwsSDEmQSlMfUkmvYp/CHciYw==`
- Generated combined Schema SHA-256: `02a4c63a638fdae4a5f6c3ad32a41a377b642c66f3abc84f6fc47c7f3d6074df`

The Codex schema fixture is generated from the pinned binary. ModelMirror's
adapter is independently written. Native command, file, Web, Skill, plugin,
authentication, configuration, arbitrary MCP, and process surfaces are rejected.
Because the pinned App Server cannot prove a stable Broker-only execution path,
its tool ownership remains `unknown` and its production capability is unavailable.

## Codex ACP mapping oracle

- Project: `agentclientprotocol/codex-acp`
- Package: `@agentclientprotocol/codex-acp` 1.6.2
- License metadata: Apache-2.0
- npm integrity: `sha512-2eF1mbs1gTqkZJSLYOun/pFDx37sYa7W63HOPezC37b/R8AYms5O1nfQu8lrqFSGDrwDZkASVORymLcqjCNqyA==`
- Declared Codex dependency: `@openai/codex ^0.148.0`

This package is not bundled or executed. It is used only as an external mapping
oracle while auditing the common protocol subset; ModelMirror does not treat it
as evidence for Codex 0.149.0-only fields.

The full Apache License 2.0 text is retained in each redistributed upstream
package and in `server/agent_upstream/vendor/penguin_harness/LICENSE` in the
source tree; the evaluation images copy that canonical license text to
`/usr/share/doc/modelmirror-coding-evaluation/APACHE-2.0.txt`.
The locked evaluation input inventory is recorded in
`server/coding_worker/evaluation_sbom.cdx.json`.
