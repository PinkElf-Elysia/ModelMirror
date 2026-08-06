"""Fixed, network-free MCP adapters for catalog wave 3.

The adapters expose opaque file identifiers instead of paths.  All input,
output and persistent-memory roots are selected by the trusted sidecar.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field


WORKSPACE_PATTERN = re.compile(r"mcpws_[0-9a-f]{32}")
FILE_ID_PATTERN = re.compile(r"mcpf_[0-9a-f]{24}")
SAFE_NAME = re.compile(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._ -]{0,119}")
MAX_INLINE_CHARS = 240_000
MAX_EXCEL_ROWS = 10_000
MAX_EXCEL_COLUMNS = 200

FileId = Annotated[
    str,
    Field(
        description="从当前受控工作区选择文件，不接受本机路径或 URI。",
        json_schema_extra={"x-modelmirror-input": "workspace-file"},
    ),
]
ArtifactName = Annotated[
    str,
    Field(
        description="产物文件名；产物只会写入可清理目录。",
        json_schema_extra={"x-modelmirror-input": "artifact-name"},
    ),
]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ARTIFACT_CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
STATE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)


def opaque_file_id(workspace_id: str, relative_path: str) -> str:
    digest = hashlib.sha256(
        f"{workspace_id}:{relative_path}".encode("utf-8")
    ).hexdigest()[:24]
    return f"mcpf_{digest}"


class WorkspaceContext:
    def __init__(self) -> None:
        workspace_id = os.getenv("MCP_FILE_WORKSPACE_ID", "").strip()
        if not WORKSPACE_PATTERN.fullmatch(workspace_id):
            raise RuntimeError("受控工作区标识无效。")
        self.workspace_id = workspace_id
        self.input_root = self._workspace_root("MCP_FILE_INPUT_ROOT", "/inputs")
        self.output_root = self._workspace_root("MCP_FILE_OUTPUT_ROOT", "/outputs")
        self.memory_root = self._workspace_root("MCP_FILE_MEMORY_ROOT", "/memory")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.memory_root.mkdir(parents=True, exist_ok=True)
        self._files: dict[str, Path] = {}
        if self.input_root.exists():
            for path in sorted(self.input_root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(self.input_root).as_posix()
                self._files[opaque_file_id(workspace_id, relative)] = path

    def _workspace_root(self, env_name: str, default: str) -> Path:
        base = Path(os.getenv(env_name, default)).resolve()
        root = (base / self.workspace_id).resolve()
        if root.parent != base:
            raise RuntimeError("受控工作区路径越界。")
        return root

    def resolve_file(self, file_id: str) -> Path:
        if not FILE_ID_PATTERN.fullmatch(str(file_id or "")):
            raise ValueError("必须选择当前工作区中的文件。")
        path = self._files.get(file_id)
        if path is None or not path.is_file() or path.is_symlink():
            raise ValueError("所选工作区文件不存在。")
        resolved = path.resolve()
        if self.input_root not in resolved.parents:
            raise ValueError("所选文件越过受控工作区。")
        return resolved

    def artifact_path(self, name: str, suffix: str) -> Path:
        clean = Path(str(name or "")).name
        if not SAFE_NAME.fullmatch(clean):
            raise ValueError("产物名称只能包含常用文字、数字、空格、点、下划线和连字符。")
        if not clean.lower().endswith(suffix.lower()):
            clean += suffix
        target = (self.output_root / clean).resolve()
        if target.parent != self.output_root or target.is_symlink():
            raise ValueError("产物路径无效。")
        return target

    @staticmethod
    def artifact_payload(path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        return {
            "artifact_name": path.name,
            "relative_path": path.name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }


def _safe_note_path(root: Path, value: str, *, allow_missing: bool = False) -> Path:
    clean = str(value or "").strip().replace("\\", "/").strip("/")
    if clean.endswith(".md"):
        clean = clean[:-3]
    if not clean or len(clean) > 240:
        raise ValueError("笔记标识不能为空且不能超过 240 个字符。")
    parts = clean.split("/")
    if any(part in {"", ".", ".."} or not SAFE_NAME.fullmatch(part) for part in parts):
        raise ValueError("笔记路径包含不安全字符。")
    target = (root / ("/".join(parts) + ".md")).resolve()
    if root not in target.parents or target.is_symlink():
        raise ValueError("笔记路径越过持久工作区。")
    if not allow_missing and not target.is_file():
        raise ValueError("笔记不存在。")
    return target


def _note_payload(path: Path, root: Path, content: str) -> dict[str, Any]:
    return {
        "note": path.relative_to(root).as_posix(),
        "content": content[:MAX_INLINE_CHARS],
        "truncated": len(content) > MAX_INLINE_CHARS,
        "updated_at": path.stat().st_mtime,
    }


def build_basic_memory(context: WorkspaceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Basic Memory")
    notes = context.memory_root / "notes"
    notes.mkdir(parents=True, exist_ok=True)

    def read(value: str) -> dict[str, Any]:
        path = _safe_note_path(notes, value)
        return _note_payload(path, notes, path.read_text(encoding="utf-8"))

    @mcp.tool(annotations=READ_ONLY)
    def read_note(note: str) -> dict[str, Any]:
        """读取当前持久记忆库中的 Markdown 笔记。"""
        return read(note)

    @mcp.tool(annotations=READ_ONLY)
    def read_content(note: str) -> dict[str, Any]:
        """读取当前持久记忆库中的 Markdown 内容。"""
        return read(note)

    @mcp.tool(annotations=READ_ONLY)
    def view_note(note: str) -> dict[str, Any]:
        """查看当前持久记忆库中的笔记。"""
        return read(note)

    @mcp.tool(annotations=READ_ONLY)
    def search_notes(query: str, limit: int = 20) -> dict[str, Any]:
        """按标题和正文关键词搜索本地笔记。"""
        clean = str(query or "").strip().casefold()
        if not clean or len(clean) > 500:
            raise ValueError("搜索词必须包含 1 到 500 个字符。")
        matches: list[dict[str, Any]] = []
        for path in sorted(notes.rglob("*.md")):
            if path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if clean in path.stem.casefold() or clean in text.casefold():
                position = text.casefold().find(clean)
                start = max(0, position - 120)
                matches.append({
                    "note": path.relative_to(notes).as_posix(),
                    "preview": text[start:start + 360],
                    "updated_at": path.stat().st_mtime,
                })
            if len(matches) >= max(1, min(int(limit), 100)):
                break
        return {"query": query, "matches": matches, "count": len(matches)}

    @mcp.tool(annotations=READ_ONLY)
    def search(query: str, limit: int = 20) -> dict[str, Any]:
        """兼容搜索入口，仅检索当前本地记忆库。"""
        return search_notes(query, limit)

    @mcp.tool(annotations=READ_ONLY)
    def fetch(note: str) -> dict[str, Any]:
        """兼容读取入口，仅访问当前本地记忆库。"""
        return read(note)

    @mcp.tool(annotations=READ_ONLY)
    def recent_activity(limit: int = 20) -> dict[str, Any]:
        """列出最近更新的本地笔记。"""
        paths = sorted(
            (path for path in notes.rglob("*.md") if not path.is_symlink()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:max(1, min(int(limit), 100))]
        return {"items": [
            {"note": path.relative_to(notes).as_posix(), "updated_at": path.stat().st_mtime}
            for path in paths
        ]}

    @mcp.tool(annotations=READ_ONLY)
    def list_directory(folder: str = "") -> dict[str, Any]:
        """列出记忆库中的 Markdown 笔记，不接受宿主目录。"""
        clean = str(folder or "").strip().replace("\\", "/").strip("/")
        target = notes if not clean else (notes / clean).resolve()
        if target != notes and notes not in target.parents:
            raise ValueError("目录越过记忆库。")
        if not target.is_dir() or target.is_symlink():
            raise ValueError("记忆目录不存在。")
        return {"folder": clean, "items": [
            {
                "name": path.name,
                "kind": "directory" if path.is_dir() else "note",
            }
            for path in sorted(target.iterdir())
            if not path.is_symlink() and (path.is_dir() or path.suffix == ".md")
        ][:500]}

    @mcp.tool(annotations=READ_ONLY)
    def build_context(note: str, depth: int = 1) -> dict[str, Any]:
        """读取笔记及最多两层 Wiki 链接上下文。"""
        depth = max(0, min(int(depth), 2))
        first = read(note)
        related: list[dict[str, Any]] = []
        frontier = re.findall(r"\[\[([^\]]+)\]\]", str(first["content"]))[:30]
        seen = {str(first["note"]).casefold()}
        for _ in range(depth):
            next_frontier: list[str] = []
            for name in frontier:
                try:
                    payload = read(name)
                except ValueError:
                    continue
                key = str(payload["note"]).casefold()
                if key in seen:
                    continue
                seen.add(key)
                related.append(payload)
                next_frontier.extend(re.findall(r"\[\[([^\]]+)\]\]", str(payload["content"]))[:10])
            frontier = next_frontier[:30]
        return {"root": first, "related": related[:50]}

    @mcp.tool(annotations=READ_ONLY)
    def basic_memory_diagnostics() -> dict[str, Any]:
        """返回本地、断网记忆库的非敏感状态。"""
        paths = [path for path in notes.rglob("*.md") if not path.is_symlink()]
        return {
            "mode": "local-only",
            "network": "disabled",
            "semantic_downloads": "disabled",
            "notes": len(paths),
        }

    @mcp.tool(annotations=STATE_WRITE)
    def write_note(title: str, content: str, folder: str = "") -> dict[str, Any]:
        """经目录审批后创建新的持久 Markdown 笔记。"""
        if not str(content).strip() or len(str(content).encode("utf-8")) > 1024 * 1024:
            raise ValueError("笔记正文不能为空且不能超过 1 MiB。")
        relative = "/".join(value for value in (str(folder).strip("/"), str(title).strip()) if value)
        path = _safe_note_path(notes, relative, allow_missing=True)
        if path.exists():
            raise ValueError("目标笔记已经存在，拒绝覆盖。")
        path.parent.mkdir(parents=True, exist_ok=True)
        body = f"---\ntitle: {title}\n---\n\n# {title}\n\n{content.strip()}\n"
        path.write_text(body, encoding="utf-8")
        return _note_payload(path, notes, body)

    @mcp.tool(annotations=STATE_WRITE)
    def edit_note(
        note: str,
        content: str,
        operation: Literal["append", "prepend", "replace"] = "append",
    ) -> dict[str, Any]:
        """经目录审批后修改已有持久笔记。"""
        path = _safe_note_path(notes, note)
        old = path.read_text(encoding="utf-8")
        if len(str(content).encode("utf-8")) > 1024 * 1024:
            raise ValueError("单次编辑内容不能超过 1 MiB。")
        if operation == "append":
            value = old.rstrip() + "\n\n" + str(content).strip() + "\n"
        elif operation == "prepend":
            value = str(content).strip() + "\n\n" + old
        else:
            value = str(content)
        path.write_text(value, encoding="utf-8")
        return _note_payload(path, notes, value)

    @mcp.tool(annotations=STATE_WRITE)
    def move_note(note: str, destination_folder: str) -> dict[str, Any]:
        """经目录审批后在当前记忆库内移动笔记。"""
        source = _safe_note_path(notes, note)
        destination = _safe_note_path(
            notes,
            f"{str(destination_folder).strip('/')}/{source.stem}",
            allow_missing=True,
        )
        if destination.exists():
            raise ValueError("目标位置已有同名笔记，拒绝覆盖。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return _note_payload(destination, notes, destination.read_text(encoding="utf-8"))

    return mcp


def _load_frame(path: Path, sheet_name: str = ""):
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    if suffix == ".json":
        return pd.read_json(path)
    return pd.read_excel(path, sheet_name=sheet_name or 0)


def _frame_records(frame, *, limit: int = 1_000) -> list[dict[str, Any]]:
    safe = frame.head(limit)
    return json.loads(safe.to_json(orient="records", force_ascii=False, date_format="iso"))


def _excel_sheet_names(path: Path) -> list[str]:
    if path.suffix.lower() in {".csv", ".tsv", ".json"}:
        return ["data"]
    import pandas as pd
    return list(pd.ExcelFile(path).sheet_names)


def build_excel(context: WorkspaceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Excel")

    @mcp.tool(annotations=READ_ONLY)
    def read_excel(file_id: FileId, sheet_name: str = "", max_rows: int = 200) -> dict[str, Any]:
        """读取受控表格文件，默认返回前 200 行，最多 1000 行。"""
        path = context.resolve_file(file_id)
        limit = max(1, min(int(max_rows), 1_000))
        frame = _load_frame(path, sheet_name)
        return {"filename": path.name, "rows": len(frame), "columns": list(map(str, frame.columns)), "data": _frame_records(frame, limit=limit), "truncated": len(frame) > limit}

    @mcp.tool(annotations=READ_ONLY)
    def get_excel_info(file_id: FileId) -> dict[str, Any]:
        """获取受控表格的大小、工作表和列信息。"""
        path = context.resolve_file(file_id)
        frame = _load_frame(path)
        return {"filename": path.name, "size_bytes": path.stat().st_size, "sheets": _excel_sheet_names(path), "rows": len(frame), "columns": list(map(str, frame.columns))}

    @mcp.tool(annotations=READ_ONLY)
    def get_sheet_names(file_id: FileId) -> dict[str, Any]:
        """列出受控表格中的工作表。"""
        path = context.resolve_file(file_id)
        return {"filename": path.name, "sheets": _excel_sheet_names(path)}

    @mcp.tool(annotations=READ_ONLY)
    def analyze_excel(file_id: FileId, sheet_name: str = "") -> dict[str, Any]:
        """对受控表格执行描述性统计。"""
        frame = _load_frame(context.resolve_file(file_id), sheet_name)
        summary = frame.describe(include="all").fillna("")
        return {"rows": len(frame), "columns": list(map(str, frame.columns)), "summary": json.loads(summary.to_json(force_ascii=False, date_format="iso"))}

    @mcp.tool(annotations=READ_ONLY)
    def filter_excel(file_id: FileId, column: str, operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains"], value: str, sheet_name: str = "", max_rows: int = 200) -> dict[str, Any]:
        """按单列条件筛选受控表格。"""
        frame = _load_frame(context.resolve_file(file_id), sheet_name)
        if column not in frame.columns:
            raise ValueError("筛选列不存在。")
        series = frame[column]
        if operator == "contains":
            mask = series.astype(str).str.contains(str(value), case=False, regex=False, na=False)
        elif operator in {"gt", "gte", "lt", "lte"}:
            numeric = series.astype(float)
            target = float(value)
            mask = {"gt": numeric > target, "gte": numeric >= target, "lt": numeric < target, "lte": numeric <= target}[operator]
        else:
            mask = series.astype(str) == str(value)
            if operator == "ne":
                mask = ~mask
        filtered = frame[mask]
        limit = max(1, min(int(max_rows), 1_000))
        return {"matched_rows": len(filtered), "data": _frame_records(filtered, limit=limit), "truncated": len(filtered) > limit}

    @mcp.tool(annotations=READ_ONLY)
    def pivot_table(file_id: FileId, index: str, values: str, aggfunc: Literal["sum", "mean", "count", "min", "max"] = "sum", columns: str = "", sheet_name: str = "") -> dict[str, Any]:
        """为受控表格生成受限透视摘要。"""
        frame = _load_frame(context.resolve_file(file_id), sheet_name)
        for name in (index, values):
            if name not in frame.columns:
                raise ValueError("透视表列不存在。")
        if columns and columns not in frame.columns:
            raise ValueError("透视表分组列不存在。")
        result = frame.pivot_table(index=index, values=values, columns=columns or None, aggfunc=aggfunc).reset_index()
        return {"data": _frame_records(result, limit=1_000), "rows": len(result)}

    @mcp.tool(annotations=READ_ONLY)
    def data_summary(file_id: FileId, sheet_name: str = "") -> dict[str, Any]:
        """返回受控表格的数据类型、缺失值和唯一值摘要。"""
        frame = _load_frame(context.resolve_file(file_id), sheet_name)
        return {"rows": len(frame), "columns": [{"name": str(name), "dtype": str(frame[name].dtype), "missing": int(frame[name].isna().sum()), "unique": int(frame[name].nunique(dropna=True))} for name in frame.columns]}

    @mcp.tool(annotations=ARTIFACT_CREATE)
    def export_chart(file_id: FileId, x_column: str, y_column: str, chart_type: Literal["line", "bar", "scatter", "histogram"] = "bar", artifact_name: ArtifactName = "chart.png", sheet_name: str = "") -> dict[str, Any]:
        """从受控表格生成 PNG 图表产物，不修改输入文件。"""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        frame = _load_frame(context.resolve_file(file_id), sheet_name)
        if x_column not in frame.columns or y_column not in frame.columns:
            raise ValueError("图表列不存在。")
        target = context.artifact_path(artifact_name, ".png")
        figure, axis = plt.subplots(figsize=(8, 5))
        if chart_type == "line": axis.plot(frame[x_column], frame[y_column])
        elif chart_type == "scatter": axis.scatter(frame[x_column], frame[y_column])
        elif chart_type == "histogram": axis.hist(frame[y_column].dropna())
        else: axis.bar(frame[x_column], frame[y_column])
        axis.set_xlabel(x_column); axis.set_ylabel(y_column); figure.tight_layout()
        figure.savefig(target, dpi=120); plt.close(figure)
        return context.artifact_payload(target)

    @mcp.tool(annotations=STATE_WRITE)
    def write_excel(data: list[dict[str, Any]], artifact_name: ArtifactName = "output.xlsx", sheet_name: str = "Sheet1") -> dict[str, Any]:
        """经审批后把受限表格数据写为新的 XLSX 产物。"""
        import pandas as pd
        if len(data) > MAX_EXCEL_ROWS or any(len(row) > MAX_EXCEL_COLUMNS for row in data):
            raise ValueError("Excel 写入最多 10000 行、200 列。")
        target = context.artifact_path(artifact_name, ".xlsx")
        pd.DataFrame(data).to_excel(target, index=False, sheet_name=str(sheet_name)[:31] or "Sheet1")
        return context.artifact_payload(target)

    @mcp.tool(annotations=STATE_WRITE)
    def update_excel(file_id: FileId, updates: list[dict[str, Any]], artifact_name: ArtifactName = "updated.xlsx", sheet_name: str = "") -> dict[str, Any]:
        """经审批后在输入副本上更新单元格并生成新 XLSX，绝不覆盖源文件。"""
        import pandas as pd
        if len(updates) > 10_000:
            raise ValueError("单次最多更新 10000 个单元格。")
        frame = _load_frame(context.resolve_file(file_id), sheet_name)
        for update in updates:
            row = int(update.get("row", 0))
            column = str(update.get("column", ""))
            if row < 0 or row >= len(frame) or column not in frame.columns:
                raise ValueError("更新位置越过表格范围。")
            frame.at[row, column] = update.get("value")
        target = context.artifact_path(artifact_name, ".xlsx")
        pd.DataFrame(frame).to_excel(target, index=False, sheet_name=(sheet_name or "Sheet1")[:31])
        return context.artifact_payload(target)

    return mcp


def _git_repository(context: WorkspaceContext) -> Path:
    if (context.input_root / ".git").is_dir():
        return context.input_root
    candidates = [path.parent for path in context.input_root.glob("*/.git") if path.is_dir()]
    if len(candidates) != 1:
        raise ValueError("工作区必须包含且只能包含一个 Git 仓库。")
    return candidates[0]


def _git(context: WorkspaceContext, *args: str, max_chars: int = MAX_INLINE_CHARS) -> dict[str, Any]:
    repository = _git_repository(context)
    command = [
        "git", "--no-optional-locks",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.fsmonitor=false",
        "-c", "credential.helper=",
        "-c", "protocol.file.allow=never",
        "-c", f"safe.directory={repository}",
        *args,
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.getenv("TMPDIR", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_EXTERNAL_DIFF": "",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(command, cwd=repository, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    output = result.stdout.decode("utf-8", errors="replace")
    error = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        raise ValueError((error or "Git 只读操作失败。")[:1_000])
    return {"output": output[:max_chars], "truncated": len(output) > max_chars}


def build_git(context: WorkspaceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror Git Read Only")

    @mcp.tool(annotations=READ_ONLY)
    def git_status() -> dict[str, Any]:
        """查看上传仓库状态；禁止锁、Hook 和任何写入。"""
        return _git(context, "status", "--short", "--branch", "--untracked-files=all")

    @mcp.tool(annotations=READ_ONLY)
    def git_diff_unstaged() -> dict[str, Any]:
        """查看未暂存差异，禁用 external diff 和 textconv。"""
        return _git(context, "diff", "--no-ext-diff", "--no-textconv")

    @mcp.tool(annotations=READ_ONLY)
    def git_diff_staged() -> dict[str, Any]:
        """查看已暂存差异，禁用 external diff 和 textconv。"""
        return _git(context, "diff", "--cached", "--no-ext-diff", "--no-textconv")

    @mcp.tool(annotations=READ_ONLY)
    def git_diff(target: str) -> dict[str, Any]:
        """查看相对指定修订的只读差异。"""
        if not re.fullmatch(r"[A-Za-z0-9_./^-]{1,200}", str(target or "")) or ".." in str(target):
            raise ValueError("Git 修订标识无效。")
        return _git(context, "diff", "--no-ext-diff", "--no-textconv", str(target))

    @mcp.tool(annotations=READ_ONLY)
    def git_log(max_count: int = 30) -> dict[str, Any]:
        """查看最多 100 条提交历史。"""
        return _git(context, "log", f"--max-count={max(1, min(int(max_count), 100))}", "--date=iso-strict", "--pretty=format:%H%x09%ad%x09%an%x09%s")

    @mcp.tool(annotations=READ_ONLY)
    def git_show(revision: str = "HEAD") -> dict[str, Any]:
        """查看指定修订，禁用 external diff 和 textconv。"""
        if not re.fullmatch(r"[A-Za-z0-9_./^-]{1,200}", str(revision or "")) or ".." in str(revision):
            raise ValueError("Git 修订标识无效。")
        return _git(context, "show", "--no-ext-diff", "--no-textconv", "--format=fuller", str(revision))

    @mcp.tool(annotations=READ_ONLY)
    def git_branch() -> dict[str, Any]:
        """列出本地分支，不创建或切换分支。"""
        return _git(context, "branch", "--list", "--format=%(refname:short)%09%(objectname:short)%09%(subject)")

    return mcp


def build_markitdown(context: WorkspaceContext) -> FastMCP:
    mcp = FastMCP("ModelMirror MarkItDown")

    @mcp.tool(annotations=ARTIFACT_CREATE)
    def convert_to_markdown(file_id: FileId, artifact_name: ArtifactName = "converted.md") -> dict[str, Any]:
        """把受控工作区文件转换为 Markdown；不接受 URL、URI 或宿主路径。"""
        from markitdown import MarkItDown
        path = context.resolve_file(file_id)
        result = MarkItDown(enable_plugins=False).convert_local(path)
        text = str(result.markdown or "")
        target = context.artifact_path(artifact_name, ".md")
        target.write_text(text, encoding="utf-8")
        payload = context.artifact_payload(target)
        payload.update({"preview": text[:20_000], "preview_truncated": len(text) > 20_000})
        return payload

    return mcp


BUILDERS = {
    "basic-memory-mcp": build_basic_memory,
    "excel-mcp-server": build_excel,
    "git-mcp": build_git,
    "markitdown-mcp": build_markitdown,
}

ADAPTER_TOOL_NAMES = {
    "basic-memory-mcp": (
        "read_note", "read_content", "view_note", "search_notes", "search",
        "fetch", "recent_activity", "list_directory", "build_context",
        "basic_memory_diagnostics", "write_note", "edit_note", "move_note",
    ),
    "excel-mcp-server": (
        "read_excel", "get_excel_info", "get_sheet_names", "analyze_excel",
        "filter_excel", "pivot_table", "data_summary", "export_chart",
        "write_excel", "update_excel",
    ),
    "git-mcp": (
        "git_status", "git_diff_unstaged", "git_diff_staged", "git_diff",
        "git_log", "git_show", "git_branch",
    ),
    "markitdown-mcp": ("convert_to_markdown",),
}


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("adapter_id", choices=sorted(BUILDERS))
    args = parser.parse_args()
    context = WorkspaceContext()
    BUILDERS[args.adapter_id](context).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
