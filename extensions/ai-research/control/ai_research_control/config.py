from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _loopback_url(value: str, *, variable: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(f"{variable} must be a plain loopback HTTP origin")
    try:
        if parsed.port is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{variable} must include a valid port") from exc
    return f"http://{parsed.hostname}:{parsed.port}"


@dataclass(frozen=True)
class Settings:
    control_db: Path
    evidence_root: Path
    inspect_log_root: Path
    worker_socket: Path
    mlflow_uri: str
    mlflow_experiment: str
    source_lock: Path
    module_boundary: Path
    poll_seconds: float
    docs_enabled: bool
    inspect_view_uri: str = "http://ai-research-inspect-view:7575"
    mlflow_public_url: str = "http://127.0.0.1:8791"
    inspect_view_public_url: str = "http://127.0.0.1:8793"

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parents[1]
        return cls(
            control_db=Path(
                os.environ.get("AI_RESEARCH_CONTROL_DB", "/data/control/control.db")
            ),
            evidence_root=Path(
                os.environ.get("AI_RESEARCH_EVIDENCE_ROOT", "/data/control/evidence")
            ),
            inspect_log_root=Path(
                os.environ.get("AI_RESEARCH_INSPECT_LOG_ROOT", "/data/inspect-logs")
            ),
            worker_socket=Path(
                os.environ.get("AI_RESEARCH_WORKER_SOCKET", "/run/ai-research/worker.sock")
            ),
            mlflow_uri=os.environ.get(
                "AI_RESEARCH_MLFLOW_URI", "http://ai-research-tracking:5000"
            ),
            mlflow_experiment=os.environ.get(
                "AI_RESEARCH_MLFLOW_EXPERIMENT", "modelmirror-ai-research-ar0"
            ),
            source_lock=root / "source-lock.json",
            module_boundary=root / "module-boundary.json",
            poll_seconds=max(
                0.1, min(float(os.environ.get("AI_RESEARCH_POLL_SECONDS", "0.5")), 5.0)
            ),
            docs_enabled=os.environ.get("AI_RESEARCH_ENABLE_DOCS", "0") == "1",
            inspect_view_uri=os.environ.get(
                "AI_RESEARCH_INSPECT_VIEW_URI", "http://ai-research-inspect-view:7575"
            ).rstrip("/"),
            mlflow_public_url=_loopback_url(
                os.environ.get("AI_RESEARCH_MLFLOW_PUBLIC_URL", "http://127.0.0.1:8791"),
                variable="AI_RESEARCH_MLFLOW_PUBLIC_URL",
            ),
            inspect_view_public_url=_loopback_url(
                os.environ.get(
                    "AI_RESEARCH_INSPECT_VIEW_PUBLIC_URL", "http://127.0.0.1:8793"
                ),
                variable="AI_RESEARCH_INSPECT_VIEW_PUBLIC_URL",
            ),
        )

    def prepare(self) -> None:
        self.control_db.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_root.mkdir(parents=True, exist_ok=True)
