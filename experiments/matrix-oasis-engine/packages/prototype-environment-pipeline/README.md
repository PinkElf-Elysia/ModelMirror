# `@matrix-oasis/prototype-environment-pipeline`

Private R10 Node workspace for planning and materializing one World Labs Marble
environment from a frozen R8 Scene Blueprint. It accepts text-only
`marble-1.1`, validates the panorama and collider bytes, and emits a canonical
Prototype Environment Bundle without prompts, remote identifiers, URLs,
credentials, or raw provider responses.

The provider never reads environment variables. Its caller supplies an API key
and an explicit asset-host allowlist. Official operation limits are one create,
at most 180 polls at ten-second intervals, one world read, and one download for
each required asset. Loopback endpoints and shorter intervals exist only for
deterministic tests.

The bundle proves local byte integrity, not provider authorship. It does not
contain SPZ, an HQ mesh, or a Scene Pack, and it is not a runtime or save format.
