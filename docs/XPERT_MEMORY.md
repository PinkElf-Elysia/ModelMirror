# Xpert Files and Memory

Last updated: 2026-07-28

## Purpose

This contract defines the ModelMirror-native file-understanding and durable-memory layer for published Xperts. It builds on the classic workflow runner and does not copy or migrate Xpert's application framework.

## Storage Model

`XpertContextStore` persists conversations, file assets and extracted artifacts, active conversation/Xpert memories, and pending/decided memory candidates. The default location is the existing runtime storage mount. Metadata is written through atomic replacement. Public APIs never expose internal file paths.

## File Contract

- Supported formats: TXT, Markdown, and text-readable PDF.
- Maximum size: 10 MB per file.
- Maximum active files: 20 per conversation.
- Maximum selected files: 5 per run.
- Model context: 10,000 characters per file and 30,000 characters total.
- Archive keeps the internal artifact so a previously started Goal can resolve its explicit reference, while new conversation runs cannot select it.

Conversation files are not automatically embedded, indexed, or attached to a knowledge
base. Knowledge writes use a separate explicit Inbox → candidate version → Evaluation
Gate → promote flow; ordinary file upload never bypasses that approval boundary.

## Memory Contract

Memory has two scopes: `conversation` is visible only inside its owning Xpert conversation; `xpert` is reusable by the same Xpert across conversations and handoff runs. Automatic recall uses deterministic local matching and recency, not embeddings. A run receives at most ten records and 8,000 characters.

User writes are explicit and active immediately. Model writes are candidates. A candidate must be approved before recall can see it; rejection is terminal. Archived memories are excluded from recall.

## Cross-Xpert Safety

Conversation files are shared with a Goal only when their IDs are included at Goal creation. The Goal carries those references through AgentTask and Handoff metadata. A target Xpert can read the shared files but cannot read the source Xpert's conversation memory. It may only recall its own Xpert-scoped records.

## Observability and Privacy

RunRegistry checkpoints may record asset IDs, candidate IDs, scope, counts, lengths, and status. They must not contain file text, memory content, full prompts, full model/tool output, local paths, embeddings, or secrets.
