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

The original R10 API remains panorama-plus-collider only. R12 adds an explicit
same-world spatial-source API that also acquires `spz_urls.full_res` and records
the provider's metric scale and ground-plane offset as bounded integer units.
That API requires an approval with exactly three downloads and emits a separate
canonical Spatial Source Bundle; remote world/operation IDs and URLs never enter
either bundle.

The bundles prove local byte integrity, not provider authorship. They are not a
Runtime Pack, Scene Pack, save format, or authenticity receipt.
