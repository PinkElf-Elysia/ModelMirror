from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
        )

    def prepare(self) -> None:
        self.control_db.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_root.mkdir(parents=True, exist_ok=True)
