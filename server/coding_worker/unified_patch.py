from __future__ import annotations

import re


_HUNK = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?(?:\r?\n)?$"
)


class UnifiedPatchError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def apply_unified_patch(path: str, original: bytes, patch: str) -> bytes:
    """Apply one path-bound UTF-8 unified patch without invoking Git or a shell."""

    try:
        original_text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnifiedPatchError(
            "Unified patches require UTF-8 text.", code="binary_change_rejected"
        ) from exc
    lines = patch.splitlines(keepends=True)
    if len(lines) < 3:
        raise UnifiedPatchError("Unified patch is incomplete.", code="patch_invalid")
    old_header = lines[0].rstrip("\r\n").split("\t", 1)[0]
    new_header = lines[1].rstrip("\r\n").split("\t", 1)[0]
    if old_header != f"--- a/{path}" or new_header != f"+++ b/{path}":
        raise UnifiedPatchError(
            "Unified patch path binding is invalid.", code="patch_invalid"
        )
    source = original_text.splitlines(keepends=True)
    output: list[str] = []
    source_index = 0
    index = 2
    while index < len(lines):
        match = _HUNK.fullmatch(lines[index])
        if match is None:
            raise UnifiedPatchError(
                "Unified patch hunk is invalid.", code="patch_invalid"
            )
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        hunk_start = max(0, old_start - 1)
        if hunk_start < source_index or hunk_start > len(source):
            raise UnifiedPatchError(
                "Unified patch hunk is out of range.", code="patch_conflict"
            )
        output.extend(source[source_index:hunk_start])
        source_index = hunk_start
        index += 1
        consumed = produced = 0
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if line.startswith("\\ No newline at end of file"):
                index += 1
                continue
            prefix, value = line[:1], line[1:]
            if prefix in {" ", "-"}:
                if source_index >= len(source) or source[source_index] != value:
                    raise UnifiedPatchError(
                        "Unified patch preimage does not match.",
                        code="patch_conflict",
                    )
                if prefix == " ":
                    output.append(value)
                    produced += 1
                source_index += 1
                consumed += 1
            elif prefix == "+":
                output.append(value)
                produced += 1
            else:
                raise UnifiedPatchError(
                    "Unified patch line is invalid.", code="patch_invalid"
                )
            index += 1
        if consumed != old_count or produced != new_count:
            raise UnifiedPatchError(
                "Unified patch hunk counts do not match.", code="patch_invalid"
            )
    output.extend(source[source_index:])
    return "".join(output).encode("utf-8")
