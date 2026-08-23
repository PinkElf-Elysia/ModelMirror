# Matrix Oasis Creator Qualification Contracts

Private R16 contract for a Creator-ready qualification manifest. The closed canonical JSON binds one source run to its R13 spatial facts, R14 solved and verified layout, and R15 replay, media, and 300-frame performance evidence.

The manifest stores hashes and bounded summaries only. It never stores the original prompt, local paths, supplier task identifiers, URLs, credentials, or raw provider responses. A valid manifest is not sufficient by itself to launch a run: the R16 qualification cache must revalidate every referenced artifact and media file before treating it as ready.
