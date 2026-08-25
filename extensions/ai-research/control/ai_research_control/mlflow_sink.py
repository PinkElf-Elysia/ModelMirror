from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from mlflow import MlflowClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status

from .evidence import finalize_mlflow, verify_receipt


class MlflowSinkError(RuntimeError):
    pass


class MlflowSink:
    def __init__(self, tracking_uri: str, experiment_name: str) -> None:
        if not tracking_uri.startswith("http://ai-research-tracking:"):
            raise MlflowSinkError("tracking URI must target the private module service")
        self.tracking_uri = tracking_uri.rstrip("/")
        self.experiment_name = experiment_name
        self.client = MlflowClient(tracking_uri=self.tracking_uri)

    def probe(self) -> str:
        endpoint = f"{self.tracking_uri}/api/2.0/mlflow/experiments/get-by-name"
        try:
            response = requests.get(
                endpoint,
                params={"experiment_name": self.experiment_name},
                timeout=2,
            )
            if response.status_code == 200:
                return self._experiment_id_from_response(response)
            if response.status_code != 404:
                raise MlflowSinkError(
                    f"MLflow experiment probe failed with status {response.status_code}"
                )

            created = requests.post(
                f"{self.tracking_uri}/api/2.0/mlflow/experiments/create",
                json={"name": self.experiment_name},
                timeout=2,
            )
            if 200 <= created.status_code < 300:
                experiment_id = str(created.json()["experiment_id"])
                if not experiment_id:
                    raise ValueError("empty experiment id")
                return experiment_id

            # Another control instance may have won the create race.
            response = requests.get(
                endpoint,
                params={"experiment_name": self.experiment_name},
                timeout=2,
            )
            if response.status_code == 200:
                return self._experiment_id_from_response(response)
            raise MlflowSinkError(
                f"MLflow experiment creation failed with status {created.status_code}"
            )
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise MlflowSinkError("MLflow experiment probe failed") from exc

    @staticmethod
    def _experiment_id_from_response(response: requests.Response) -> str:
        experiment_id = str(response.json()["experiment"]["experiment_id"])
        if not experiment_id:
            raise ValueError("empty experiment id")
        return experiment_id

    def sync(
        self,
        run: dict[str, Any],
        receipt: dict[str, Any],
        evidence_dir: Path,
    ) -> tuple[str, dict[str, Any]]:
        experiment_id = self._experiment_id()
        mlflow_run_id = self._find_or_create_run(experiment_id, run)
        trace_id = self._export_trace(experiment_id, run["run_id"])
        final_receipt = finalize_mlflow(
            evidence_dir,
            receipt,
            experiment_id=experiment_id,
            run_id=mlflow_run_id,
            trace_id=trace_id,
            synced_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        verify_receipt(evidence_dir, final_receipt)

        params = {
            "fixture_id": run["fixture_id"],
            "case_id": run["case_id"],
            "claim_level": "harness_only",
            "pack_status": "fixture_only",
            "inspect_status": str(run.get("inspect_status") or "unknown"),
            "outcome": str(run.get("outcome") or "unknown"),
            "replay_verified": str(bool(run.get("replay_verified"))).lower(),
        }
        for key, value in params.items():
            self.client.log_param(mlflow_run_id, key, value)
        duration = self._duration_seconds(run.get("started_at"), run.get("terminal_at"))
        if duration is not None:
            self.client.log_metric(mlflow_run_id, "duration_seconds", duration)
        self.client.log_metric(
            mlflow_run_id,
            "artifact_count",
            float(len(final_receipt.get("artifacts") or {})),
        )
        for path in sorted(evidence_dir.iterdir()):
            if path.is_file() and not path.is_symlink():
                self.client.log_artifact(mlflow_run_id, str(path), artifact_path="evidence")
        status = {
            "success": "FINISHED",
            "task_error": "FAILED",
            "cancelled": "KILLED",
            "infrastructure_error": "FAILED",
        }.get(run.get("outcome"), "FAILED")
        self.client.set_terminated(mlflow_run_id, status=status)
        return mlflow_run_id, final_receipt

    def _experiment_id(self) -> str:
        experiment = self.client.get_experiment_by_name(self.experiment_name)
        if experiment is not None:
            return experiment.experiment_id
        try:
            return self.client.create_experiment(self.experiment_name)
        except Exception:
            experiment = self.client.get_experiment_by_name(self.experiment_name)
            if experiment is None:
                raise
            return experiment.experiment_id

    def _find_or_create_run(self, experiment_id: str, run: dict[str, Any]) -> str:
        filter_string = f"tags.modelmirror.run_id = '{run['run_id']}'"
        existing = self.client.search_runs(
            experiment_ids=[experiment_id], filter_string=filter_string, max_results=2
        )
        if len(existing) > 1:
            raise MlflowSinkError("multiple MLflow runs share one control run id")
        if existing:
            return existing[0].info.run_id
        created = self.client.create_run(
            experiment_id,
            tags={
                "modelmirror.run_id": run["run_id"],
                "modelmirror.module": "ai-research",
                "modelmirror.claim_level": "harness_only",
                "modelmirror.pack_status": "fixture_only",
                "modelmirror.tenant_id": "local",
            },
            run_name=run["run_id"],
        )
        return created.info.run_id

    def _export_trace(self, experiment_id: str, control_run_id: str) -> str:
        digest = hashlib.sha256(control_run_id.encode("utf-8")).digest()
        trace_id_bytes = digest[:16]
        span_id_bytes = digest[16:24]
        now_ns = time.time_ns()
        request = ExportTraceServiceRequest()
        resource_spans = request.resource_spans.add()
        resource_spans.resource.attributes.extend(
            [
                KeyValue(key="service.name", value=AnyValue(string_value="modelmirror-ai-research")),
                KeyValue(key="modelmirror.run_id", value=AnyValue(string_value=control_run_id)),
            ]
        )
        scope_spans = resource_spans.scope_spans.add()
        scope_spans.scope.name = "modelmirror.ai-research.ar0"
        span = scope_spans.spans.add(
            trace_id=trace_id_bytes,
            span_id=span_id_bytes,
            name="fixture-evidence-sync",
            kind=Span.SPAN_KIND_INTERNAL,
            start_time_unix_nano=now_ns,
            end_time_unix_nano=now_ns + 1_000_000,
        )
        span.attributes.extend(
            [KeyValue(key="claim.level", value=AnyValue(string_value="harness_only"))]
        )
        span.status.CopyFrom(Status(code=Status.STATUS_CODE_OK))
        response = requests.post(
            f"{self.tracking_uri}/v1/traces",
            headers={
                "Content-Type": "application/x-protobuf",
                "x-mlflow-experiment-id": experiment_id,
            },
            data=request.SerializeToString(),
            timeout=10,
        )
        if response.status_code >= 300:
            raise MlflowSinkError(f"OTLP export failed with status {response.status_code}")
        return f"tr-{trace_id_bytes.hex()}"

    @staticmethod
    def _duration_seconds(start: str | None, end: str | None) -> float | None:
        if not start or not end:
            return None
        try:
            started = datetime.fromisoformat(start.replace("Z", "+00:00"))
            terminal = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0.0, (terminal - started).total_seconds())
