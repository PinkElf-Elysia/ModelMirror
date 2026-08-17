from __future__ import annotations


BATCH_SERVING_SUFFIX = ":batch"


def is_realtime_chat_model_id(value: object) -> bool:
    """Return whether a catalog ID can be sent to Chat Completions."""

    model_id = str(value or "").strip().lower()
    return bool(model_id) and not model_id.endswith(BATCH_SERVING_SUFFIX)
