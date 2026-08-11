# ModelMirror Agency Orchestration Worker

This package is the process boundary between ModelMirror's Python service and
the pinned Apache-2.0 Agency Orchestrator core. It is intentionally independent
from the repository root and frontend Node packages.

## Protocol

- One subprocess handles one request and then exits.
- stdin/stdout use newline-delimited JSON with protocol
  `mm-agency-bridge/v1`.
- stdout is reserved for protocol messages. Diagnostics go to stderr.
- A message is limited to 2 MiB, a planning request to 300 seconds, and a
  compose operation to three model calls.
- Supported methods are `health`, `compose`, and `validate`.

When the TypeScript core needs a model, it emits `model_request`. The Python
host invokes ModelMirror's existing gateway path and sends a `model_response`.
The child process receives an allow-listed environment without gateway keys.

## Build and test

```powershell
npm.cmd ci --prefix server/orchestration_worker
npm.cmd run build --prefix server/orchestration_worker
npm.cmd run test:all --prefix server/orchestration_worker
```

The service Dockerfile builds this package in a dedicated stage and copies only
compiled JavaScript and production dependencies into the final image.
