from __future__ import annotations

from datetime import date
from typing import Mapping

from .models import (
    ALLOWED_PROMPT_PLACEHOLDERS,
    AgentModelConfig,
    AgentSystemConfig,
    CompactionConfig,
    ToolConfig,
    ToolDefinitionConfig,
)


DEFAULT_SYSTEM_PROMPT = """# Role
You are ModelMirror General Agent, an agent that completes the user's requests in the assigned Workspace with the tools available to you.

# Personality
Communicate precisely and concisely, with warmth, and reply in the user's language. Code, identifiers, and commit messages keep their own conventions. Do not repeatedly explain tools or restate their results.

# Success criteria
- Before delivering the result, check that every problem in the request has been solved.
- Verify work through available means; never claim a result you did not observe.

# Constraints
- Make the smallest change that satisfies the request; do not modify unrelated files.
- Destructive operations are forbidden.
- Never stop a process you did not start.
- If a tool call fails, read the error, adjust, and retry; never repeat the same failing input.
- Never ask the user to paste an API key or secret into the conversation.

# Stop rules
- Stop and give the final answer once the success criteria are met.
- If the request is materially ambiguous, ask for clarification instead of guessing.
- If an error cannot be resolved, stop and report the blocker.

# Tool use
- Inspect real files and the runtime environment instead of guessing.
- Work only inside the Workspace shown in the Environment section.
- Mention each created or updated file by its Workspace-relative path.

# System markers
Some messages contain system-generated blocks that are context, not user text:
- `[turn_aborted]`: continue from the recorded partial work without repeating completed calls.
- `[turn_retried]`: adjust after the recorded failed attempt.
- `[context_summary]`: treat the summary as the established record.
- `[user_steering]`: incorporate the user's mid-run direction into the active task.

# Suggested workflows
- For a long task, keep a concise plan and update it as work lands.
- Delegate self-contained subtasks with `run_subagent` when it is available.
- Keep intermediate files in the Session scratchpad and final deliverables in the Workspace.

[developer_instructions]
Custom instructions from the editable AGENTS.md.

{{AGENTS_MD}}
[/developer_instructions]

# Skills
Skills are reusable instruction packages installed in this Agent State. Read an available Skill's `SKILL.md` in full when the request matches it. Skills marked unavailable or reference-only must not be treated as executable capabilities.
{{SKILL_METADATA}}

# Environment
- Platform: {{PLATFORM}}
- OS Version: {{OS_VERSION}}
- Shell: {{SHELL}}
- Date: {{DATE}}
- App Data Dir: {{PROJECT_DIR}}
- Agent ID: {{AGENT_ID}}
- CWD: {{CWD}}
- Provider: {{PROVIDER}}
- Model ID: {{MODEL_ID}}
- Session ID: {{SESSION_ID}}"""

DEFAULT_COMPACTION_PROMPT = (
    "Summarize the task transcript above. The summary will replace the transcript "
    "as its only record, so include everything needed to continue the task: the "
    "original request, current state, next steps, and learnings. Do not call tools; "
    "reply with text only, in exactly this format and nothing after it:\n\n"
    "[summary]put the summary text here...[/summary]"
)


def _schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, object],
    permission: str,
    timeout_ms: int,
    max_output: int,
    call_description: bool = False,
) -> ToolDefinitionConfig:
    return ToolDefinitionConfig(
        name=name,
        description=description,
        parameters=parameters,
        permission=permission,
        timeoutMs=timeout_ms,
        maxOutputLength=max_output,
        call_description=call_description,
    )


def default_tools() -> ToolConfig:
    return ToolConfig(
        builtin=[
            _tool(
                name="read_file",
                description="Read a UTF-8 text file in the Workspace with optional line offset and limit.",
                parameters=_schema(
                    {
                        "file_path": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                    },
                    ["file_path"],
                ),
                permission="r",
                timeout_ms=30_000,
                max_output=64_000,
            ),
            _tool(
                name="edit_file",
                description="Replace exact text in an existing Workspace file.",
                parameters=_schema(
                    {
                        "file_path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "replace_all": {"type": "boolean", "default": False},
                    },
                    ["file_path", "old_text", "new_text"],
                ),
                permission="rw",
                timeout_ms=30_000,
                max_output=16_000,
            ),
            _tool(
                name="write_file",
                description="Create or replace a UTF-8 text file in the Workspace.",
                parameters=_schema(
                    {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    ["file_path", "content"],
                ),
                permission="rw",
                timeout_ms=30_000,
                max_output=16_000,
            ),
            _tool(
                name="exec_command",
                description="Start a command in the Workspace and return output or a process id.",
                parameters=_schema(
                    {
                        "command": {"type": "string"},
                        "yield_time_ms": {"type": "integer", "minimum": 250},
                        "description": {"type": "string"},
                    },
                    ["command"],
                ),
                permission="rw",
                timeout_ms=120_000,
                max_output=16_000,
                call_description=True,
            ),
            _tool(
                name="input_command",
                description="Poll or send input to a command started by this Session.",
                parameters=_schema(
                    {
                        "process_id": {"type": "string"},
                        "input": {"type": "string"},
                        "yield_time_ms": {"type": "integer", "minimum": 250},
                        "description": {"type": "string"},
                    },
                    ["process_id"],
                ),
                permission="rw",
                timeout_ms=130_000,
                max_output=16_000,
                call_description=True,
            ),
            _tool(
                name="run_subagent",
                description="Delegate one self-contained subtask to a child Agent Session.",
                parameters=_schema(
                    {
                        "prompt": {"type": "string"},
                        "agent_id": {"type": "string"},
                        "background": {"type": "boolean", "default": False},
                        "yield_time_ms": {"type": "integer", "minimum": 250},
                        "description": {"type": "string"},
                    },
                    ["prompt"],
                ),
                permission="rw",
                timeout_ms=600_000,
                max_output=16_000,
                call_description=True,
            ),
            _tool(
                name="input_subagent",
                description="Poll or send a follow-up to a child Agent Session.",
                parameters=_schema(
                    {
                        "subagent_id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "yield_time_ms": {"type": "integer", "minimum": 250},
                        "description": {"type": "string"},
                    },
                    ["subagent_id"],
                ),
                permission="rw",
                timeout_ms=600_000,
                max_output=16_000,
                call_description=True,
            ),
            _tool(
                name="read_image",
                description="Read a local image from the Workspace for a vision-capable model.",
                parameters=_schema(
                    {"file_path": {"type": "string"}},
                    ["file_path"],
                ),
                permission="r",
                timeout_ms=60_000,
                max_output=16_000,
            ),
            _tool(
                name="describe_image",
                description="Describe a local Workspace image through the configured vision fallback.",
                parameters=_schema(
                    {
                        "file_path": {"type": "string"},
                        "prompt": {"type": "string"},
                    },
                    ["file_path"],
                ),
                permission="r",
                timeout_ms=90_000,
                max_output=16_000,
            ),
        ]
    )


def default_system_config(
    *,
    name: str = "General Agent",
    description: str = "General-purpose agent that completes the user's requests with its tools.",
) -> AgentSystemConfig:
    return AgentSystemConfig(
        version=1,
        name=name,
        description=description,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        max_turns=100,
        model=AgentModelConfig(
            max_tokens=32_000,
            thinking_level="medium",
            timeoutMs=120_000,
        ),
        compaction=CompactionConfig(
            max_context_length=128_000,
            max_session_turns=-1,
            mode="summarize",
            prompt=DEFAULT_COMPACTION_PROMPT,
        ),
        tools=default_tools(),
        skillset_id="general-agent-default",
    )


def render_system_prompt(
    template: str,
    *,
    values: Mapping[str, str] | None = None,
) -> str:
    replacements = {key: "" for key in ALLOWED_PROMPT_PLACEHOLDERS}
    replacements["DATE"] = date.today().isoformat()
    replacements.update({key: str(value) for key, value in (values or {}).items()})
    unknown = sorted(set(replacements) - ALLOWED_PROMPT_PLACEHOLDERS)
    if unknown:
        raise ValueError(f"unknown prompt replacement keys: {', '.join(unknown)}")
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered
