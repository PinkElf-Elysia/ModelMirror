---
name: agent-creation
description: Create a validated ModelMirror Agent State from one concrete user requirement without overwriting an existing Agent.
short_description: Turn one requirement into a working Agent State.
short_description_zh: 把一句需求变成可验证的智能体配置。
version: 8
updated: 2026-08-05T00:00:00Z
---

# Agent Creation

> ModelMirror modification notice: adapted from PenguinHarness for the native Agent State staging, validation, and atomic-promotion contract; unsupported product capabilities were removed.

Turn a concrete requirement into a complete candidate Agent State. This skill describes the files to produce; the ModelMirror backend owns validation and the final atomic promotion into the Agent library.

## When to proceed

If the user only names this skill and gives no desired behavior, ask what the Agent should do. If the user has already supplied a concrete one-line requirement, proceed without asking them to restate it. Derive conservative defaults and report the assumptions at the end.

## Resolve identity and runtime

- Choose a short `agent_id` containing only letters, digits, `_`, or `-`.
- Never reuse or overwrite an existing Agent ID. If an ID conflicts, stop and report it.
- Give the Agent a recognizable display `name` and a concise `description`.
- Inherit the Builder Session's model choice; do not persist provider credentials or API keys in Agent State.
- Inherit `model.thinking_level` unless the user explicitly requests another supported value.

## Candidate layout

Write only inside the staging directory supplied by the runtime:

```text
<staging>/<agent_id>/
├── agent_state/
│   ├── system_config.yaml
│   ├── AGENTS.md
│   ├── skills/<skill_id>/
│   ├── memory/
│   └── tools/
└── scratchpad/
```

Never write directly into the live `agents/` directory. The backend promotes a valid candidate atomically after the task completes.

## Create configuration

Copy the General Agent configuration supplied in staging as the base. Preserve its stable `system_prompt`, compaction settings, and nine-tool schema. Change only:

- top-level `name`, `description`, and `version: 1`;
- `model.thinking_level` when resolved above;
- requirement-specific behavior in `agent_state/AGENTS.md`;
- the minimum required Skill snapshots.

Do not add unknown YAML fields. Do not add Vault, MCP, model-provider, cost, trace, evaluation, schedule, or workflow-node configuration; those capabilities are not part of this Agent State version.

## Write AGENTS.md

`agent_state/AGENTS.md` is the requirement-specific instruction layer and must be non-empty. Keep it concise and operational:

- Role: what the Agent is responsible for.
- Workflow: the steps it should normally follow.
- Domain rules: concrete constraints derived from the request.
- Success criteria: what must be true before it reports completion.
- Stop rules: when it should ask for clarification or report a blocker.

Do not claim tools, data, credentials, integrations, or permissions the Agent does not have.

## Select Skills

A Skill snapshot is a directory under `agent_state/skills/<skill_id>/` containing `SKILL.md` and its packaged assets. Select only Skills whose library capability status is runnable. Never inject reference-only, dependency-missing, or unavailable Skills into the runtime context.

Use the built-in library metadata supplied to the Session; do not fetch or install an external Skill while generating an Agent. Copy complete directories so the new Agent receives an immutable snapshot. Do not merely name Skills in AGENTS.md.

Typical choices:

- General software work: `software-engineering`.
- Web interface work: `software-engineering` plus `web-design`.
- Data analysis deliverables: `data-analysis`.
- Presentation deliverables: `bento-slides`.
- Porting a user-supplied Skill later: `skill-porting`.

## Validate before handoff

Before reporting the candidate ready:

- parse `system_config.yaml` and confirm the exact supported schema;
- confirm `name`, `description`, and positive `version`;
- confirm `AGENTS.md` exists and is non-empty;
- confirm every Skill directory has a parseable `SKILL.md` whose `name` matches the directory;
- confirm every installed Skill was marked runnable in the supplied library metadata;
- confirm no path outside the supplied staging directory changed.

Report the candidate Agent ID, display name, assumptions, inherited runtime, selected Skills, and validation result. Never claim the Agent was installed; only the backend can promote it.
