from __future__ import annotations

import argparse
import json
from pathlib import PurePosixPath


DENIED_SUFFIXES = {".bat", ".cmd", ".com", ".exe", ".msi", ".ps1", ".sh"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    with open(args.context, encoding="utf-8") as handle:
        context = json.load(handle)
    arguments = context.get("arguments")
    path_value = arguments.get("path", "") if isinstance(arguments, dict) else ""
    suffix = PurePosixPath(str(path_value).replace("\\", "/")).suffix.casefold()
    denied = suffix in DENIED_SUFFIXES
    output = (
        {
            "type": "deny",
            "code": "release_extension_denied",
            "message": "Executable release file extensions are not allowed.",
        }
        if denied
        else {
            "type": "validation",
            "code": "release_name_allowed",
            "passed": True,
            "message": "The release file extension is allowed.",
        }
    )
    with open(args.result, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            {"version": "modelmirror-hook-result-v1", "outputs": [output]},
            handle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
