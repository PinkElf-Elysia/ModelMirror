from __future__ import annotations

import re
from dataclasses import dataclass

from .models import ResolvedPromptProfile
from .store import PromptProfileValidationError


COMMAND_PATTERN = re.compile(r"^/([a-z0-9][a-z0-9_-]{0,31})(?:\s+(.*))?$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class PromptCommandResolution:
    original_message: str
    effective_message: str
    escaped: bool = False
    alias: str | None = None
    profile_id: str | None = None
    profile_version: int | None = None
    source: str | None = None


def resolve_prompt_command(
    message: str,
    profiles: list[ResolvedPromptProfile],
    *,
    allow_plugin: bool = True,
    require_public: bool = False,
) -> PromptCommandResolution:
    original = str(message or "")
    if original.startswith("//"):
        return PromptCommandResolution(
            original_message=original,
            effective_message=original[1:],
            escaped=True,
        )
    if not original.startswith("/"):
        return PromptCommandResolution(
            original_message=original,
            effective_message=original,
        )
    match = COMMAND_PATTERN.fullmatch(original)
    if match is None:
        raise PromptProfileValidationError(
            "Slash command format is /alias followed by optional text. "
            "Use // to send a literal leading slash."
        )
    alias = match.group(1).lower()
    args = match.group(2) or ""
    if len(args) > 8_000:
        raise PromptProfileValidationError(
            "Prompt command arguments are limited to 8000 characters."
        )
    eligible = [
        profile
        for profile in profiles
        if (allow_plugin or profile.source != "plugin")
        and (not require_public or profile.public_app_allowed)
    ]
    for profile in eligible:
        if alias not in {value.lower() for value in profile.aliases}:
            continue
        rendered = re.sub(r"{{\s*args\s*}}", args, profile.template)
        return PromptCommandResolution(
            original_message=original,
            effective_message=rendered,
            alias=alias,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            source=profile.source,
        )
    available = sorted(
        {
            alias_name
            for profile in eligible
            for alias_name in profile.aliases
        }
    )
    hint = ", ".join(f"/{value}" for value in available[:12])
    raise PromptProfileValidationError(
        f"Unknown Prompt command: /{alias}. "
        + (f"Available commands: {hint}." if hint else "No commands are bound.")
    )
