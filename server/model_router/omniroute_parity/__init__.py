"""MIT-licensed OmniRoute routing behavior adapted for ModelMirror."""

from .intent import TaskClassification, classify_prompt_intent, classify_task
from .selection import (
    ALGORITHM_VERSION,
    CONFIG_HASH,
    LEGACY_ALGORITHM_VERSION,
    NativeSelection,
    build_competitive_frontier,
    get_task_fitness,
    select_ranked_candidate,
    speed_factors,
    speed_score,
)

__all__ = [
    "ALGORITHM_VERSION",
    "CONFIG_HASH",
    "LEGACY_ALGORITHM_VERSION",
    "NativeSelection",
    "TaskClassification",
    "build_competitive_frontier",
    "classify_prompt_intent",
    "classify_task",
    "get_task_fitness",
    "select_ranked_candidate",
    "speed_factors",
    "speed_score",
]
