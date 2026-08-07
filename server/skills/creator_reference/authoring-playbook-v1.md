<!--
playbook-version: claude-aligned-authoring-v1

Modified by the ModelMirror project from the creation-stage guidance in
Anthropic's skill-creator, Copyright 2026 Anthropic, PBC, licensed under
Apache-2.0. This local version narrows the workflow to drafting only; it does
not include Anthropic's evaluation loop, description optimizer, HTML viewer,
or packaging utilities. See LICENSE-ANTHROPIC-SKILL-CREATOR.txt and the root
THIRD_PARTY_NOTICES.md. Anthropic does not endorse this modification.
-->

# ModelMirror Skill authoring playbook

Use this playbook only inside the trusted `skill-creator-assistant-v1` draft
generator. Produce a complete, reviewable draft; never claim that the draft has
passed behavioral evaluation. PR3 owns baseline/candidate evaluation and the
installation quality gate.

## 1. Turn the session into explicit requirements

Read the intent, positive examples, near-miss examples, expected output,
success criteria, and selected evidence. Do not merely restate them.

- Treat each non-empty session field as a requirement with its supplied ID.
- Infer the repeatable job, the inputs it expects, and the observable output.
- Use near-misses to state boundaries and failure behavior, not as supported
  examples.
- Preserve uncertainty as an assumption. Do not invent facts, APIs, files, or
  credentials to make the package look complete.
- If evidence conflicts, choose the safer behavior and explain the assumption
  in `design.assumptions`.

## 2. Design the reusable workflow before writing files

Walk through each positive example as if executing it from scratch. Identify
what is repeatedly rediscovered or rewritten, then decide whether that belongs
in the main instructions or a bundled resource.

Create a typed `design` object with:

- `workflow_steps`: four to ten ordered items. Give every step a stable `id`
  and an executable instruction. Include decision points and verification.
- `output_contract`: one or more concrete output fields, formats, or artifacts.
- `failure_modes`: the conditions that require refusal, clarification,
  explicit missing-data markers, or a documented degraded result.
- `resources`: one item for every packaged file except `agents/openai.yaml`.
  Record `path`, `purpose`, and the workflow step IDs that use it.
- `assumptions`: only assumptions that remain material to correct execution.
- `requirement_coverage`: one entry per required session ID. Each entry must
  point to one or more real Markdown headings using
  `{path: "SKILL.md", section: "Exact heading"}` or another packaged Markdown
  file. Abstract step IDs alone are not coverage evidence.

Prefer the least complex resource that makes repeated execution more reliable:

- Put deterministic, repeatedly rewritten operations in `scripts/`.
- Put detailed domain rules, schemas, or policies in `references/`.
- Put text templates or boilerplate intended for final output in `assets/`.
- Do not create a resource that is unused, duplicated in `SKILL.md`, or only
  decorative.

## 3. Write frontmatter for reliable triggering

Use a short verb-led kebab-case name. The root directory and frontmatter name
must match exactly.

Write an 80–1024 character description that says both:

1. what capability the Skill provides; and
2. the concrete requests, inputs, tools, or situations in which it should be
   used.

When near-miss examples establish an important boundary, include a compact
"do not use" condition. Do not hide triggering guidance only in the body,
because the body is loaded after selection.

## 4. Write executable SKILL.md instructions

Use concise imperative language. Assume the consuming Agent is capable; spend
context on non-obvious process, domain rules, and guardrails.

Include at least these clearly named sections:

- Purpose or scope: supported job, inputs, prerequisites, and boundaries.
- Workflow: at least four ordered, executable steps. State when to read or run
  each resource.
- Output contract: required fields, ordering, format, missing-value markers,
  and evidence/citation behavior where applicable.
- Quality checks: deterministic checks and observable completion conditions.
- Failure handling or degradation: unsupported inputs, missing dependencies,
  unavailable network, ambiguous evidence, and partial results.
- Resources: every bundled file, its purpose, and when to load or run it.

Keep core procedural guidance in `SKILL.md`. Move lengthy variant rules and
reference data out one level, and link them directly. Do not make references
link to deeper references. Add a table of contents to a reference longer than
100 lines.

## 5. Keep the package minimal and honest

- Include only UTF-8 text under `scripts/`, `references/`, `assets/`, and the
  optional `agents/openai.yaml` compatibility file.
- Do not add README, changelog, installation guide, evaluation files, or
  process notes to the Skill package.
- Never include secrets, raw conversation traces, environment variables,
  attachments, Sandbox files, or local absolute paths.
- Never retain TODO, placeholder, or "fill this later" text in an AI-generated
  draft.
- Do not claim a script works merely because it parses. Give it conservative
  failure behavior and leave runtime evaluation to the evaluation phase.
- Do not create `evals/` in PR2. The Creator session stores test design in PR3.

## 6. Return one typed proposal

Return exactly one successful proposal tool call using this wrapper:

```json
{
  "creator_contract_version": "skill-creator-contract-v1",
  "skill": {
    "name": "verb-led-name",
    "description": "Capability and triggering situations",
    "skill_markdown": "---\nname: ...\ndescription: ...\n---\n...",
    "files": {}
  },
  "design": {
    "workflow_steps": [],
    "output_contract": [],
    "failure_modes": [],
    "resources": [],
    "assumptions": [],
    "requirement_coverage": []
  }
}
```

Before calling the proposal tool, check that every resource exists, every
resource is referenced from `SKILL.md`, every session requirement has a real
Markdown location, and the instructions can be followed without seeing the
Creator conversation.
