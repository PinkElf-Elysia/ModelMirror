"""Reviewed arXiv LaTeX public-read compatibility contract.

The upstream project identity is fetching and interpreting arXiv LaTeX
sources.  This adapter keeps that identity while accepting only canonical
arXiv identifiers and using two fixed official HTTPS endpoints.  Source
archives are parsed in memory, never extracted or executed.
"""

from __future__ import annotations

import io
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ConfigDict

from .safe_http import SafeHttpClient


ARXIV_LATEX_ADAPTER_ID = "takashiishida-arxiv-latex-mcp"
ARXIV_HOSTS = frozenset({"export.arxiv.org"})
ARXIV_UPSTREAM_LOCK = {
    "version": "v0.2.2",
    "commit": "481d8169262dd6f5a6ab04f767da8a8b2e9789bf",
    "license": "MIT",
    "repository": "takashiishida/arxiv-latex-mcp",
}

ARXIV_ID = re.compile(
    r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v[1-9][0-9]{0,2})?",
    re.IGNORECASE,
)
SECTION_PATH = re.compile(r"(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3}|[A-Za-z][A-Za-z0-9 _.-]{0,79})")
SECTION = re.compile(
    r"\\(?P<kind>section|subsection|subsubsection)\*?\s*\{(?P<title>(?:[^{}]|\{[^{}]*\}){1,300})\}",
    re.IGNORECASE,
)
COMMENT = re.compile(r"(?<!\\)%[^\r\n]*")
ALLOWED_SOURCE_SUFFIXES = frozenset({".tex", ".ltx"})

MAX_ARCHIVE_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 200
MAX_MEMBER_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_CHARS = 160_000
MAX_SECTION_CHARS = 40_000

READ_ONLY_NETWORK = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _freeze_strict_tool_contract(mcp: FastMCP) -> FastMCP:
    for tool in mcp._tool_manager._tools.values():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = ConfigDict(
            **dict(argument_model.model_config),
            extra="forbid",
        )
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)
    return mcp


def canonical_arxiv_id(value: object) -> str:
    clean = str(value or "").strip()
    if clean.lower().startswith("arxiv:"):
        clean = clean[6:].strip()
    if not ARXIV_ID.fullmatch(clean):
        raise ValueError("arxiv_id_invalid")
    return clean


def _client() -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=ARXIV_HOSTS,
        timeout=20.0,
        # The fixed /e-print endpoint canonicalizes once to /src on the same
        # reviewed host. SafeHttpClient revalidates host and pinned DNS for the
        # redirect, so this does not admit another origin.
        max_redirects=1,
        max_response_bytes=MAX_ARCHIVE_BYTES,
        minimum_intervals={"export.arxiv.org": 3.0},
    )


def _require_ok(status: int) -> None:
    if status == 429:
        raise ValueError("arxiv_rate_limited")
    if status >= 500:
        raise ValueError("arxiv_upstream_unavailable")
    if status != 200:
        raise ValueError("arxiv_paper_unavailable")


def _safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    path = PurePosixPath(member.name.replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or member.issym()
        or member.islnk()
    ):
        raise ValueError("arxiv_source_archive_denied")
    if member.isdir():
        return path
    if not member.isfile() or member.size < 0 or member.size > MAX_MEMBER_BYTES:
        raise ValueError("arxiv_source_archive_denied")
    return path


def _decode_source(data: bytes) -> str:
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("arxiv_source_encoding_invalid")


def parse_source_archive(body: bytes) -> str:
    if not body or len(body) > MAX_ARCHIVE_BYTES:
        raise ValueError("arxiv_source_size_exceeded")
    files: list[tuple[str, str]] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_MEMBERS:
                raise ValueError("arxiv_source_archive_denied")
            total = 0
            for member in members:
                path = _safe_member(member)
                if member.isdir():
                    continue
                total += member.size
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("arxiv_source_size_exceeded")
                if path.suffix.lower() not in ALLOWED_SOURCE_SUFFIXES:
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("arxiv_source_archive_denied")
                value = stream.read(MAX_MEMBER_BYTES + 1)
                if len(value) != member.size or len(value) > MAX_MEMBER_BYTES:
                    raise ValueError("arxiv_source_archive_denied")
                files.append((path.as_posix(), _decode_source(value)))
    except tarfile.ReadError:
        files = [("paper.tex", _decode_source(body))]
    if not files:
        raise ValueError("arxiv_latex_source_missing")
    files.sort(key=lambda item: (0 if item[0].lower().endswith("main.tex") else 1, item[0]))
    text = "\n\n".join(f"% source: {name}\n{content}" for name, content in files)
    text = COMMENT.sub("", text).replace("\x00", "")
    if not text.strip():
        raise ValueError("arxiv_latex_source_missing")
    return text[:MAX_SOURCE_CHARS]


def parse_abstract_feed(body: bytes, arxiv_id: str) -> dict[str, Any]:
    if not body or len(body) > MAX_ARCHIVE_BYTES:
        raise ValueError("arxiv_response_invalid")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("arxiv_response_invalid") from exc
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries = root.findall("atom:entry", ns)
    if len(entries) != 1:
        raise ValueError("arxiv_paper_unavailable")
    entry = entries[0]

    def text(path: str, maximum: int) -> str:
        value = " ".join((entry.findtext(path, default="", namespaces=ns) or "").split())
        return value[:maximum]

    authors = [
        " ".join((node.findtext("atom:name", default="", namespaces=ns) or "").split())[:200]
        for node in entry.findall("atom:author", ns)[:50]
    ]
    return {
        "arxiv_id": arxiv_id,
        "title": text("atom:title", 1_000),
        "abstract": text("atom:summary", 20_000),
        "authors": [item for item in authors if item],
        "published": text("atom:published", 64),
        "updated": text("atom:updated", 64),
    }


def _sections(source: str) -> list[dict[str, Any]]:
    matches = list(SECTION.finditer(source))
    values: list[dict[str, Any]] = []
    counters = [0, 0, 0]
    for index, match in enumerate(matches):
        level = {"section": 0, "subsection": 1, "subsubsection": 2}[match.group("kind").lower()]
        counters[level] += 1
        for child in range(level + 1, len(counters)):
            counters[child] = 0
        path = ".".join(str(item) for item in counters[: level + 1])
        title = re.sub(r"\s+", " ", match.group("title")).strip()[:300]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        content = source[match.end() : end].strip()
        values.append({"path": path, "title": title, "content": content})
    return values[:500]


def build_arxiv_latex() -> FastMCP:
    mcp = FastMCP("ModelMirror arXiv LaTeX")
    client = _client()
    source_cache: dict[str, str] = {}

    def source(arxiv_id: str) -> str:
        key = canonical_arxiv_id(arxiv_id)
        if key not in source_cache:
            response = client.request(
                f"https://export.arxiv.org/e-print/{quote(key, safe='/')}",
                headers={"Accept": "application/x-eprint-tar", "User-Agent": "ModelMirror-arXiv-LaTeX/0.2.2-compatible"},
                max_response_bytes=MAX_ARCHIVE_BYTES,
            )
            _require_ok(response.status)
            source_cache[key] = parse_source_archive(response.body)
        return source_cache[key]

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_paper_prompt(arxiv_id: str) -> dict[str, Any]:
        """Return bounded LaTeX source for one canonical arXiv identifier."""
        key = canonical_arxiv_id(arxiv_id)
        value = source(key)
        return {"arxiv_id": key, "latex": value, "truncated": len(value) >= MAX_SOURCE_CHARS}

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_paper_abstract(arxiv_id: str) -> dict[str, Any]:
        """Return bounded public metadata and abstract for one arXiv paper."""
        key = canonical_arxiv_id(arxiv_id)
        response = client.request(
            "https://export.arxiv.org/api/query?id_list=" + quote(key, safe=""),
            headers={"Accept": "application/atom+xml", "User-Agent": "ModelMirror-arXiv-LaTeX/0.2.2-compatible"},
        )
        _require_ok(response.status)
        return parse_abstract_feed(response.body, key)

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def list_paper_sections(arxiv_id: str) -> dict[str, Any]:
        """List bounded LaTeX section paths without executing source content."""
        key = canonical_arxiv_id(arxiv_id)
        values = _sections(source(key))
        return {
            "arxiv_id": key,
            "sections": [{"path": item["path"], "title": item["title"]} for item in values],
            "count": len(values),
        }

    @mcp.tool(annotations=READ_ONLY_NETWORK)
    def get_paper_section(arxiv_id: str, section_path: str) -> dict[str, Any]:
        """Return one bounded section selected from the parsed source outline."""
        key = canonical_arxiv_id(arxiv_id)
        selected = str(section_path or "").strip()
        if not SECTION_PATH.fullmatch(selected):
            raise ValueError("arxiv_section_path_invalid")
        values = _sections(source(key))
        match = next(
            (item for item in values if item["path"] == selected or item["title"].casefold() == selected.casefold()),
            None,
        )
        if match is None:
            raise ValueError("arxiv_section_not_found")
        content = str(match["content"])[:MAX_SECTION_CHARS]
        return {
            "arxiv_id": key,
            "path": match["path"],
            "title": match["title"],
            "latex": content,
            "truncated": len(str(match["content"])) > len(content),
        }

    return _freeze_strict_tool_contract(mcp)


WAVE29_PUBLIC_BUILDERS = {ARXIV_LATEX_ADAPTER_ID: build_arxiv_latex}
WAVE29_PUBLIC_TOOL_NAMES = {
    ARXIV_LATEX_ADAPTER_ID: (
        "get_paper_prompt",
        "get_paper_abstract",
        "list_paper_sections",
        "get_paper_section",
    )
}
