---
name: release-file-guard
description: Check release file names before writing published artifacts.
---

# Release file guard

Before `sandbox_write_file`, run the guard declared in `hooks/manifest.json`.
The guard allows ordinary release files and denies executable extensions.
