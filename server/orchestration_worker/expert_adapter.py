from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .contracts import AgencyAgentDefinition


def _field(record: Any, name: str, default: str = "") -> str:
    if isinstance(record, Mapping):
        value = record.get(name, default)
    else:
        value = getattr(record, name, default)
    return str(value or default).strip()


def adapt_expert_catalog(
    records: Iterable[Any],
    *,
    max_system_prompt_chars: int = 2_048,
) -> list[AgencyAgentDefinition]:
    """Map ModelMirror's catalog without creating a second role directory.

    The bounded prompt excerpt keeps the all-expert automatic catalog below
    the 2 MiB bridge message limit. Compose uses the role summary; execution
    remains on ModelMirror's existing expert path and full prompt store.
    """

    if not 256 <= max_system_prompt_chars <= 16_000:
        raise ValueError("max_system_prompt_chars is invalid")
    agents: list[AgencyAgentDefinition] = []
    seen: set[str] = set()
    for record in records:
        agent_id = _field(record, "id")
        if not agent_id or agent_id in seen:
            raise ValueError(f"Expert id is missing or duplicated: {agent_id or '<empty>'}")
        seen.add(agent_id)
        prompt = _field(record, "prompt")[:max_system_prompt_chars].strip()
        description = _field(record, "expertise") or _field(record, "description")
        agents.append(
            AgencyAgentDefinition(
                id=agent_id,
                path=agent_id,
                name=_field(record, "name"),
                department=_field(record, "department", "未分类"),
                description=description,
                system_prompt=prompt,
                emoji=_field(record, "emoji") or None,
            )
        )
    return agents
