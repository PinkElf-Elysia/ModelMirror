"""Shared context optimization primitives.

The package is intentionally small in phase 0. Deterministic compression and
the Xpert Runtime adapter are introduced only after routing parity gates pass.
"""

from .core import (
    CompressionProfile,
    CompressionReport,
    ContextOptimization,
    deterministic_compress,
    estimate_messages_tokens,
    estimate_text_tokens,
    optimize_context,
    protected_markers,
)

CONTEXT_ENGINE_VERSION = "modelmirror-context-v1"

__all__ = [
    "CONTEXT_ENGINE_VERSION",
    "CompressionProfile",
    "CompressionReport",
    "ContextOptimization",
    "deterministic_compress",
    "estimate_messages_tokens",
    "estimate_text_tokens",
    "optimize_context",
    "protected_markers",
]
