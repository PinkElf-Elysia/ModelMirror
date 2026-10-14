from __future__ import annotations


BATCH_SERVING_SUFFIX = ":batch"


def is_realtime_chat_model_id(value: object) -> bool:
    """Exclude serving contracts incompatible with ordinary streaming chat."""

    model_id = str(value or "").strip().lower()
    return (bool(model_id) and not model_id.endswith(BATCH_SERVING_SUFFIX)
            and model_id not in {"scaledown/summarize", "scaledown/extract", "scaledown/classify-1"})
