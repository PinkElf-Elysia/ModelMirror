# RPG-01 contract reference

All four formats use exact `formatVersion: "0.1.0"`, strict closed objects, JSON Schema Draft 2020-12, and an additional pure semantic pass. Unknown fields fail. Package-owned stable IDs are globally unique across the package, provenance, resources, information fields, and state fields; display names may repeat.

## Card package

`modelmirror.ai-rpg.card-package` is one logical JSON document. It carries package metadata, rights and source references with SHA-256 values, worlds, identities, talents, items, backgrounds, styles, worldbook entries, openings, declarative information modules, declarative commands, minimal typed state fields, and plugin requirements.

- `extensions` is the only private-data escape hatch. Its top-level keys must be namespaced. An iterative preflight rejects non-JSON/cyclic values, excessive depth or node counts, and executable-shaped keys including compound forms such as `scriptSource`, `toolCallV2`, or `autoInstallPlugin`. Ordinary inert metadata such as `description` remains valid. The core preserves accepted JSON values but gives them no economic, task, save, death, rebirth, settlement, or inheritance meaning.
- Worldbook entries freeze content, source, tags, visibility, and world scope only. Retrieval, ordering, token budget, and injection placement are later-round concerns.
- `requiredPlugins` block readiness when missing or incompatible. Every `recommendedPlugins` entry declares a `core`, `omit`, or `readOnly` fallback.
- ZIP, files, media, imports, transformations, prompts, runtime state machines, and dynamic plugin behavior are outside this format.

## Player setup

`modelmirror.ai-rpg.player-setup` binds a card version to a player-defined character and opening. Package references and typed custom resources are supported. It separates inherent background from current identity, identity rank from character power, and talent ownership from activation. `runtimePermissions` must be empty, so fictional text such as `系统核心权限·root` never grants host or plugin authority.

## Turn exchange

`modelmirror.ai-rpg.turn-exchange` accepts `action`, `speech`, `query`, or a card-declared `command`. The proposal is exactly:

- `narrative`
- `suggestedActions`
- `informationModules`
- `stateProposals`
- `uncertainties`

Suggestions contain no selection, execution, commit, or embedded state effect. A query cannot contain a state proposal. State proposals can target only a declared `modelMayPropose` boolean, integer, short-text, or enum field and remain proposals for a later authoritative runtime. Information-module values are checked against the card declaration. HTML-like text remains untrusted opaque content and is not proof of sanitization.

## Plugin manifest and readiness

`modelmirror.ai-rpg.plugin-manifest` declares identity, exact version, host-contract compatibility, a closed capability and permission vocabulary, settings descriptors, exact dependencies, data read/proposal scopes, no network or ModelMirror-mediated network scope, fixed lifecycle promises, source, license, and hashes. It contains no module path, command, entrypoint, installation hook, loader, or executable payload.

`evaluatePluginReadiness` starts from the card's required and recommended roots and validates only their reachable manifest dependency closures. Unrelated supplied manifests do not affect a card's readiness. Missing, invalid, incompatible, or cyclic required closures are errors. The same failures anywhere in a recommended closure are warnings whose code includes the chosen fallback, so that root safely degrades and `ready` remains true when there is no required-closure error. A manifest must explicitly support host contract `0.1.0`. The report contains no install, enable, update, or authorization action and is not a registry-wide manifest linter.

## Stable reports

`validate*` returns `{ valid, diagnostics }`; readiness returns `{ ready, diagnostics }`. Phases are `schema`, `reference`, `policy`, and `readiness`; severities are `error` and `warning`. Diagnostics are deduplicated and sorted by phase, severity, path, code, then related path. JSON Pointers use RFC 6901 escaping where schema-controlled keys are appended. Ajv internals, input values, absolute paths, and stacks are never returned.

## Downstream consumers

- RPG-02 consumes card/player contracts to build a small, provenance-preserving content compiler and archive boundary. It must not add runtime or UI semantics to the package.
- RPG-03 consumes turn/plugin contracts to build the isolated runtime, ModelMirror adapter, receipts, and minimal plugin host. Readiness remains separate from installation, activation, and authorization.
- RPG-04 consumes declared style, worldbook, state, and output shapes to design the original host prompt and deterministic context assembly. It must not infer a retrieval algorithm from RPG-01.
- RPG-05 consumes suggestions and information modules for the player UI. It must render strings safely and keep player selection separate from model proposals.
