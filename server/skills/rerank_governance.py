from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from .finder import MAX_QUERY_LENGTH, _normalize
from .package_validation import scan_skill_package_credentials
from .rerank_evaluation import SkillRerankEvaluator
from .semantic_rerank import (
    SkillRerankOutcome,
    SkillRerankRequest,
    SkillSearchIndexV1,
    _fingerprint,
)
from .semantic_rerank_service import (
    MARKET_TIMEOUT_SECONDS,
    ROUTER_TIMEOUT_SECONDS,
    SEMANTIC_STRATEGY_VERSION,
    SkillSemanticRerankService,
)


GOVERNANCE_SCHEMA_VERSION = 1
GOVERNANCE_VERSION = "skill-rerank-governance-v1"
MAX_FEEDBACK_RECORDS = 2_000
MAX_SHADOW_RECORDS = 2_000
RETENTION_SECONDS = 30 * 24 * 60 * 60
FeedbackJudgment = Literal["relevant", "not_relevant"]


class SkillRerankGovernanceError(RuntimeError):
    def __init__(self, message: str, *, code: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class SkillRerankGovernanceConflict(SkillRerankGovernanceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="skill_rerank_revision_conflict", status_code=409)


class SkillRerankGovernanceUnavailable(SkillRerankGovernanceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="skill_rerank_governance_unavailable", status_code=503)


def _storage_path() -> Path:
    configured = os.getenv("SKILL_RERANK_STORAGE_DIR", "").strip()
    directory = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parent / "storage"
    )
    return directory / "skill_rerank_governance.json"


def _now() -> float:
    return time.time()


def _safe_text(value: Any, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        return ""
    return text


def _require_no_credentials(value: str, *, label: str) -> None:
    if scan_skill_package_credentials(skill_markdown=value):
        raise SkillRerankGovernanceError(
            f"{label} appears to contain a credential and was not stored.",
            code="skill_rerank_sensitive_input",
        )


class SkillRerankGovernanceStore:
    """Atomic local Store for explicit feedback, evaluations and promotion receipts."""

    def __init__(self, snapshot_path: str | Path | None = None) -> None:
        self.snapshot_path = Path(snapshot_path or _storage_path())
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._state = self._empty_state()
        self._load()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schemaVersion": GOVERNANCE_SCHEMA_VERSION,
            "revision": 1,
            "actorId": f"console_{uuid.uuid4().hex}",
            "feedback": [],
            "shadowReceipts": [],
            "evaluations": {},
            "policy": {
                "revision": 1,
                "mode": "shadow",
                "promotion": None,
                "updatedAt": _now(),
            },
        }

    @property
    def available(self) -> bool:
        return self._load_error is None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def actor_id(self) -> str:
        with self._lock:
            return str(self._state["actorId"])

    def summary(self) -> dict[str, Any]:
        with self._lock:
            state = self._clean_copy(self._state)
            return {
                "available": self.available,
                "revision": int(state["revision"]),
                "feedbackCount": len(state["feedback"]),
                "shadowReceiptCount": len(state["shadowReceipts"]),
                "evaluationCount": len(state["evaluations"]),
                "policy": copy.deepcopy(state["policy"]),
            }

    def list_feedback(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            state = self._clean_copy(self._state)
            return copy.deepcopy(state["feedback"][-max(1, min(limit, 2_000)) :][::-1])

    def add_feedback(
        self,
        *,
        expected_revision: int,
        query: str,
        query_hash: str,
        candidate_id: str,
        candidate_fingerprint: str,
        judgment: FeedbackJudgment,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        _require_no_credentials(query, label="Skill search feedback query")
        record = {
            "feedbackId": f"skill_feedback_{uuid.uuid4().hex}",
            "createdAt": _now(),
            "query": query,
            "queryHash": query_hash,
            "candidateId": candidate_id,
            "candidateFingerprint": candidate_fingerprint,
            "judgment": judgment,
            "lexicalRank": self._rank(receipt.get("lexicalRanks"), candidate_id),
            "semanticRank": self._rank(receipt.get("proposedRanks"), candidate_id),
            "rankingReceiptFingerprint": _fingerprint(receipt),
            "provider": _safe_text(receipt.get("provider"), maximum=80) or "none",
            "model": _safe_text(receipt.get("model"), maximum=256) or None,
            "strategyVersion": _safe_text(
                receipt.get("strategyVersion"), maximum=120
            ),
        }

        def mutate(state: dict[str, Any]) -> None:
            self._expect_revision(state, expected_revision)
            state["feedback"].append(record)

        self._mutate(mutate)
        return copy.deepcopy(record)

    def clear_feedback(self, *, expected_revision: int) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            self._expect_revision(state, expected_revision)
            state["feedback"] = []

        self._mutate(mutate)
        return self.summary()

    def record_shadow(self, receipt: dict[str, Any]) -> None:
        safe = {
            "recordedAt": _now(),
            "status": _safe_text(receipt.get("status"), maximum=40),
            "queryHash": _safe_text(receipt.get("queryHash"), maximum=64),
            "candidateSetFingerprint": _safe_text(
                receipt.get("candidateSetFingerprint"), maximum=64
            ),
            "candidateFingerprints": copy.deepcopy(
                receipt.get("candidateFingerprints", [])[:24]
                if isinstance(receipt.get("candidateFingerprints"), list)
                else []
            ),
            "rankChanges": copy.deepcopy(
                receipt.get("rankChanges", [])[:24]
                if isinstance(receipt.get("rankChanges"), list)
                else []
            ),
            "durationMs": max(0, int(receipt.get("durationMs") or 0)),
            "provider": _safe_text(receipt.get("provider"), maximum=80) or "none",
            "model": _safe_text(receipt.get("model"), maximum=256) or None,
            "strategyVersion": _safe_text(
                receipt.get("strategyVersion"), maximum=120
            ),
            "fallbackReason": _safe_text(
                receipt.get("fallbackReason"), maximum=120
            )
            or None,
        }
        if not safe["queryHash"]:
            return

        def mutate(state: dict[str, Any]) -> None:
            state["shadowReceipts"].append(safe)

        # Shadow telemetry is high-frequency runtime evidence, not a control-plane
        # decision. Persist it atomically without invalidating feedback/evaluation
        # optimistic revisions on every Router request.
        self._mutate(mutate, advance_revision=False)

    def shadow_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = self._clean_copy(self._state)["shadowReceipts"]
        durations = [int(row.get("durationMs") or 0) for row in rows]
        fallbacks = [row for row in rows if row.get("fallbackReason")]
        changed = [row for row in rows if row.get("rankChanges")]
        return {
            "sampleCount": len(rows),
            "changedCount": len(changed),
            "fallbackCount": len(fallbacks),
            "fallbackRate": round(len(fallbacks) / len(rows), 6) if rows else 0.0,
            "p95DurationMs": self._percentile(durations, 0.95),
            "fallbackReasons": self._counts(
                str(row.get("fallbackReason")) for row in fallbacks
            ),
        }

    def create_evaluation(self, payload: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        record = {
            "evaluationId": f"skill_rerank_eval_{uuid.uuid4().hex}",
            "revision": 1,
            "status": "queued",
            "createdAt": _now(),
            "startedAt": None,
            "completedAt": None,
            "errorCode": None,
            **copy.deepcopy(payload),
        }

        def mutate(state: dict[str, Any]) -> None:
            self._expect_revision(state, expected_revision)
            if any(
                item.get("status") in {"queued", "running"}
                for item in state["evaluations"].values()
            ):
                raise SkillRerankGovernanceConflict(
                    "A Skill rerank evaluation is already running."
                )
            state["evaluations"][record["evaluationId"]] = record

        self._mutate(mutate)
        return copy.deepcopy(record)

    def update_evaluation(
        self,
        evaluation_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        updated: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            nonlocal updated
            current = state["evaluations"].get(evaluation_id)
            if not isinstance(current, dict):
                raise SkillRerankGovernanceError(
                    "Skill rerank evaluation was not found.",
                    code="skill_rerank_evaluation_not_found",
                    status_code=404,
                )
            if int(current.get("revision") or 0) != int(expected_revision):
                raise SkillRerankGovernanceConflict(
                    "Skill rerank evaluation revision changed."
                )
            current.update(copy.deepcopy(changes))
            current["revision"] = int(current["revision"]) + 1
            updated = current

        self._mutate(mutate)
        return copy.deepcopy(updated)

    def require_evaluation(self, evaluation_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._state["evaluations"].get(evaluation_id)
            if not isinstance(record, dict):
                raise SkillRerankGovernanceError(
                    "Skill rerank evaluation was not found.",
                    code="skill_rerank_evaluation_not_found",
                    status_code=404,
                )
            return copy.deepcopy(record)

    def list_evaluations(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._state["evaluations"].values())
        rows.sort(key=lambda row: float(row.get("createdAt") or 0), reverse=True)
        return copy.deepcopy(rows[: max(1, min(limit, 100))])

    def promote(
        self,
        *,
        evaluation: dict[str, Any],
        expected_revision: int,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            nonlocal result
            self._expect_revision(state, expected_revision)
            policy = state["policy"]
            policy["revision"] = int(policy.get("revision") or 0) + 1
            policy["mode"] = "on"
            policy["promotion"] = copy.deepcopy(receipt)
            policy["updatedAt"] = _now()
            result = policy

        self._mutate(mutate)
        return copy.deepcopy(result)

    def rollback(self, *, expected_revision: int) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def mutate(state: dict[str, Any]) -> None:
            nonlocal result
            self._expect_revision(state, expected_revision)
            policy = state["policy"]
            policy["revision"] = int(policy.get("revision") or 0) + 1
            policy["mode"] = "shadow"
            policy["promotion"] = None
            policy["updatedAt"] = _now()
            result = policy

        self._mutate(mutate)
        return copy.deepcopy(result)

    @staticmethod
    def _rank(values: Any, candidate_id: str) -> int | None:
        if not isinstance(values, list) or candidate_id not in values:
            return None
        return values.index(candidate_id) + 1

    @staticmethod
    def _counts(values: Any) -> dict[str, int]:
        result: dict[str, int] = {}
        for value in values:
            result[value] = result.get(value, 0) + 1
        return dict(sorted(result.items()))

    @staticmethod
    def _percentile(values: list[int], fraction: float) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        index = max(0, math.ceil(len(ordered) * fraction) - 1)
        return ordered[index]

    @staticmethod
    def _expect_revision(state: dict[str, Any], expected: int) -> None:
        if int(state.get("revision") or 0) != int(expected):
            raise SkillRerankGovernanceConflict(
                "Skill rerank governance revision changed."
            )

    def _clean_copy(self, state: dict[str, Any]) -> dict[str, Any]:
        clean = copy.deepcopy(state)
        cutoff = _now() - RETENTION_SECONDS
        clean["feedback"] = [
            row for row in clean["feedback"] if float(row.get("createdAt") or 0) >= cutoff
        ][-MAX_FEEDBACK_RECORDS:]
        clean["shadowReceipts"] = [
            row
            for row in clean["shadowReceipts"]
            if float(row.get("recordedAt") or 0) >= cutoff
        ][-MAX_SHADOW_RECORDS:]
        return clean

    def _mutate(self, callback: Any, *, advance_revision: bool = True) -> None:
        with self._lock:
            self._ensure_writable()
            candidate = self._clean_copy(self._state)
            callback(candidate)
            if advance_revision:
                candidate["revision"] = int(candidate.get("revision") or 0) + 1
            self._save(candidate)
            self._state = candidate

    def _ensure_writable(self) -> None:
        if self._load_error:
            raise SkillRerankGovernanceUnavailable(
                "Skill rerank governance storage is unavailable and remains fail-closed."
            )

    def _load(self) -> None:
        if not self.snapshot_path.exists():
            return
        try:
            raw = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("schemaVersion") != GOVERNANCE_SCHEMA_VERSION
                or not isinstance(raw.get("revision"), int)
                or not isinstance(raw.get("actorId"), str)
                or not isinstance(raw.get("feedback"), list)
                or not isinstance(raw.get("shadowReceipts"), list)
                or not isinstance(raw.get("evaluations"), dict)
                or not isinstance(raw.get("policy"), dict)
            ):
                raise ValueError("invalid Skill rerank governance snapshot")
            self._state = self._clean_copy(raw)
            for evaluation in self._state["evaluations"].values():
                if isinstance(evaluation, dict) and evaluation.get("status") in {
                    "queued",
                    "running",
                }:
                    evaluation["status"] = "failed"
                    evaluation["errorCode"] = "evaluation_interrupted"
                    evaluation["completedAt"] = _now()
                    evaluation["revision"] = int(evaluation.get("revision") or 0) + 1
        except Exception:
            self._load_error = "skill_rerank_governance_snapshot_invalid"

    def _save(self, state: dict[str, Any]) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.snapshot_path.with_suffix(
            f"{self.snapshot_path.suffix}.{uuid.uuid4().hex}.tmp"
        )
        payload = json.dumps(
            state, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.snapshot_path)
        finally:
            temporary.unlink(missing_ok=True)


class SkillRerankGovernanceService:
    def __init__(
        self,
        *,
        rerank_service: SkillSemanticRerankService,
        store: SkillRerankGovernanceStore | None = None,
        evaluator: SkillRerankEvaluator | None = None,
    ) -> None:
        self.rerank_service = rerank_service
        self.search_index: SkillSearchIndexV1 = rerank_service.search_index
        self.store = store or SkillRerankGovernanceStore()
        self.evaluator = evaluator or SkillRerankEvaluator(search_index=self.search_index)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def configure_reranker(self) -> None:
        self.rerank_service.configure_governance(
            router_mode_resolver=self.effective_router_mode,
            router_identity_validator=self.validate_router_identity,
            shadow_receipt_sink=self.record_shadow_receipt,
        )

    def validate_router_identity(self, provider: str, model: str | None) -> bool:
        if not self.store.available:
            return False
        summary = self.store.summary()
        promotion = summary["policy"].get("promotion")
        if not isinstance(promotion, dict):
            return False
        if promotion.get("provider") == provider and promotion.get("model") == model:
            return True
        try:
            self.store.rollback(expected_revision=summary["revision"])
        except SkillRerankGovernanceError:
            pass
        return False

    def status(self) -> dict[str, Any]:
        semantic = self.rerank_service.status()
        summary = self.store.summary()
        effective, reasons = self.effective_router_mode_details()
        return {
            **semantic,
            "governanceVersion": GOVERNANCE_VERSION,
            "governanceAvailable": summary["available"],
            "governanceRevision": summary["revision"],
            "feedbackCount": summary["feedbackCount"],
            "evaluationCount": summary["evaluationCount"],
            "evaluations": self.store.list_evaluations(limit=10)
            if summary["available"]
            else [],
            "policy": summary["policy"],
            "effectiveRouterMode": effective,
            "policyReasons": reasons,
            "shadow": self.store.shadow_summary() if summary["available"] else None,
        }

    def effective_router_mode(self) -> Literal["off", "shadow", "on"]:
        return self.effective_router_mode_details()[0]

    def effective_router_mode_details(
        self,
    ) -> tuple[Literal["off", "shadow", "on"], list[str]]:
        if self.rerank_service.config.router_mode == "off":
            return "off", ["semantic_router_disabled_by_environment"]
        if not self.store.available:
            return "off", ["skill_rerank_governance_unavailable"]
        summary = self.store.summary()
        policy = summary["policy"]
        promotion = policy.get("promotion")
        if policy.get("mode") != "on" or not isinstance(promotion, dict):
            return "shadow", ["semantic_router_not_promoted"]
        current = self._binding()
        mismatches = [
            code
            for field, code in (
                ("searchIndexFingerprint", "search_index_changed"),
                ("runtimeIndexFingerprint", "runtime_index_changed"),
                ("trustIndexFingerprint", "trust_index_changed"),
                ("strategyVersion", "semantic_strategy_changed"),
                ("semanticConfigFingerprint", "semantic_provider_changed"),
            )
            if promotion.get(field) != current.get(field)
        ]
        if mismatches:
            return "shadow", mismatches
        return "on", []

    def record_shadow_receipt(self, payload: dict[str, Any]) -> None:
        if not self.store.available or self.effective_router_mode() != "shadow":
            return
        try:
            self.store.record_shadow(payload)
        except SkillRerankGovernanceError:
            return

    async def search_market(self, request: SkillRerankRequest) -> SkillRerankOutcome:
        if request.semantic and not self.store.available:
            lexical = await self.rerank_service.search(
                SkillRerankRequest(
                    query=request.query,
                    scope="market",
                    limit=request.limit,
                    semantic=False,
                )
            )
            return self.rerank_service._lexical_outcome(
                query=request.query,
                lexical_results=lexical.lexical_results,
                limit=request.limit,
                status="lexical_fallback",
                fallback_reason="skill_rerank_governance_unavailable",
                warnings=("skill_rerank_governance_unavailable",),
            )
        return await self.rerank_service.search(request)

    def record_feedback(
        self,
        *,
        expected_revision: int,
        query: str,
        candidate_id: str,
        candidate_fingerprint: str,
        judgment: FeedbackJudgment,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        _require_no_credentials(query, label="Skill search feedback query")
        try:
            if len(json.dumps(receipt, ensure_ascii=False)) > 131_072:
                raise SkillRerankGovernanceError(
                    "Ranking receipt is too large.",
                    code="skill_rerank_feedback_invalid",
                )
        except (TypeError, ValueError) as exc:
            raise SkillRerankGovernanceError(
                "Ranking receipt is invalid.", code="skill_rerank_feedback_invalid"
            ) from exc
        normalized = _normalize(str(query or "")[:MAX_QUERY_LENGTH])
        if len(normalized) < 2:
            raise SkillRerankGovernanceError(
                "A normalized Skill search query is required.",
                code="skill_rerank_feedback_invalid",
            )
        query_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        if receipt.get("queryHash") != query_hash:
            raise SkillRerankGovernanceConflict("Ranking receipt query changed.")
        if receipt.get("strategyVersion") != SEMANTIC_STRATEGY_VERSION:
            raise SkillRerankGovernanceConflict("Ranking strategy changed.")
        if (
            receipt.get("fallbackReason")
            or receipt.get("provider") not in {"api", "llm"}
            or not isinstance(receipt.get("semanticRanks"), list)
            or not receipt.get("semanticRanks")
        ):
            raise SkillRerankGovernanceError(
                "Feedback requires a completed semantic ranking receipt.",
                code="skill_rerank_feedback_not_semantic",
            )
        current = self.search_index.candidate_by_id(candidate_id)
        if not current or current.get("candidateFingerprint") != candidate_fingerprint:
            raise SkillRerankGovernanceConflict("Skill candidate changed.")
        fingerprint_rows = receipt.get("candidateFingerprints")
        if not isinstance(fingerprint_rows, list) or not any(
            isinstance(row, dict)
            and row.get("candidateId") == candidate_id
            and row.get("candidateFingerprint") == candidate_fingerprint
            for row in fingerprint_rows
        ):
            raise SkillRerankGovernanceConflict(
                "Ranking receipt does not bind the selected Skill candidate."
            )
        return self.store.add_feedback(
            expected_revision=expected_revision,
            query=normalized,
            query_hash=query_hash,
            candidate_id=candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            judgment=judgment,
            receipt=receipt,
        )

    def start_evaluation(
        self, *, expected_revision: int, schedule: bool = True
    ) -> dict[str, Any]:
        binding = self._binding()
        record = self.store.create_evaluation(
            {
                **binding,
                "provider": self.rerank_service.config.provider,
                "model": None,
                "baseline": None,
                "semantic": None,
                "feedbackSummary": None,
                "gates": [],
                "eligibleForPromotion": False,
            },
            expected_revision=expected_revision,
        )
        evaluation_id = record["evaluationId"]
        if not schedule:
            return record
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return record
        task = loop.create_task(self._run_evaluation(evaluation_id))
        self._tasks[evaluation_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(evaluation_id, None))
        return record

    async def run_evaluation_now(self, evaluation_id: str) -> dict[str, Any]:
        await self._run_evaluation(evaluation_id)
        return self.store.require_evaluation(evaluation_id)

    async def _run_evaluation(self, evaluation_id: str) -> None:
        current = self.store.require_evaluation(evaluation_id)
        if current.get("status") not in {"queued", "running"}:
            return
        current = self.store.update_evaluation(
            evaluation_id,
            expected_revision=int(current["revision"]),
            changes={"status": "running", "startedAt": _now()},
        )
        try:
            baseline = self.evaluator.evaluate()
            payload = self.evaluator.load_cases()
            reports: list[dict[str, Any]] = []
            durations: list[int] = []
            provider_successes = 0
            provider_attempts = 0
            provider_identities: set[tuple[str, str]] = set()
            policy_violations = 0
            for case in payload["cases"]:
                lexical = self.search_index.lexical_search(
                    SkillRerankRequest(
                        query=case["query"],
                        scope=case["scope"],
                        limit=6,
                        semantic=False,
                    )
                )
                outcome = await self.rerank_service.rerank_lexical_results(
                    query=case["query"],
                    lexical_results=lexical.lexical_results,
                    scope=case["scope"],
                    limit=6,
                    timeout_seconds=(
                        MARKET_TIMEOUT_SECONDS
                        if case["scope"] == "market"
                        else ROUTER_TIMEOUT_SECONDS
                    ),
                )
                receipt = outcome.receipt
                lexical_ids = list(receipt.lexical_ranks)
                proposed_ids = list(receipt.proposed_ranks)
                if (
                    len(proposed_ids) != len(set(proposed_ids))
                    or set(proposed_ids) != set(lexical_ids)
                ):
                    policy_violations += 1
                if receipt.fallback_reason != "no_public_candidates":
                    provider_attempts += 1
                if outcome.status in {"semantic", "shadow"} and not receipt.fallback_reason:
                    provider_successes += 1
                    provider_identities.add((receipt.provider, receipt.model or ""))
                durations.append(receipt.duration_ms)
                reports.append(
                    self._case_report(
                        case=case,
                        lexical_ids=lexical_ids,
                        proposed_ids=proposed_ids,
                        status=outcome.status,
                        fallback_reason=receipt.fallback_reason,
                        duration_ms=receipt.duration_ms,
                    )
                )
            semantic = self._aggregate_reports(reports)
            semantic["policyViolationCount"] += policy_violations
            semantic["providerSuccessRate"] = round(
                provider_successes / provider_attempts, 6
            ) if provider_attempts else 1.0
            semantic["providerAttemptCount"] = provider_attempts
            semantic["p95DurationMs"] = SkillRerankGovernanceStore._percentile(
                durations, 0.95
            )
            semantic["providerIdentities"] = [
                {"provider": provider, "model": model or None}
                for provider, model in sorted(provider_identities)
            ]
            gates = self._promotion_gates(
                baseline=baseline.serialize(), semantic=semantic, case_count=len(reports)
            )
            identity = next(iter(provider_identities)) if len(provider_identities) == 1 else None
            self.store.update_evaluation(
                evaluation_id,
                expected_revision=int(current["revision"]),
                changes={
                    "status": "completed",
                    "completedAt": _now(),
                    "provider": identity[0] if identity else "mixed_or_unavailable",
                    "model": identity[1] if identity and identity[1] else None,
                    "baseline": baseline.serialize(),
                    "semantic": semantic,
                    "feedbackSummary": self._feedback_summary(),
                    "gates": gates,
                    "eligibleForPromotion": all(gate["passed"] for gate in gates),
                    "caseReports": reports,
                },
            )
        except Exception as exc:
            error_code = (
                exc.code
                if isinstance(exc, SkillRerankGovernanceError)
                else "skill_rerank_evaluation_failed"
            )
            latest = self.store.require_evaluation(evaluation_id)
            self.store.update_evaluation(
                evaluation_id,
                expected_revision=int(latest["revision"]),
                changes={
                    "status": "failed",
                    "completedAt": _now(),
                    "errorCode": error_code,
                },
            )

    def promote(
        self,
        *,
        evaluation_id: str,
        expected_revision: int,
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise SkillRerankGovernanceError(
                "Skill rerank promotion requires explicit confirmation.",
                code="skill_rerank_promotion_confirmation_required",
            )
        evaluation = self.store.require_evaluation(evaluation_id)
        if evaluation.get("status") != "completed" or not evaluation.get(
            "eligibleForPromotion"
        ):
            raise SkillRerankGovernanceConflict(
                "Skill rerank evaluation does not satisfy promotion gates."
            )
        if any(evaluation.get(key) != value for key, value in self._binding().items()):
            raise SkillRerankGovernanceConflict(
                "Skill rerank evaluation no longer matches the current indexes or provider."
            )
        receipt = {
            "promotionId": f"skill_rerank_promotion_{uuid.uuid4().hex}",
            "evaluationId": evaluation_id,
            "promotedAt": _now(),
            "actorKind": "local_console",
            "actorId": self.store.actor_id,
            "provider": evaluation.get("provider"),
            "model": evaluation.get("model"),
            **self._binding(),
        }
        return self.store.promote(
            evaluation=evaluation,
            expected_revision=expected_revision,
            receipt=receipt,
        )

    def rollback(self, *, expected_revision: int) -> dict[str, Any]:
        return self.store.rollback(expected_revision=expected_revision)

    def policy(self) -> dict[str, Any]:
        return self.status()

    def _binding(self) -> dict[str, Any]:
        payload = self.search_index._load()
        config = self.rerank_service.config
        return {
            "searchIndexFingerprint": payload["fingerprint"],
            "runtimeIndexFingerprint": payload["runtimeIndexFingerprint"],
            "trustIndexFingerprint": payload["trustIndexFingerprint"],
            "strategyVersion": SEMANTIC_STRATEGY_VERSION,
            "semanticConfigFingerprint": _fingerprint(
                {
                    "provider": config.provider,
                    "apiEndpointHash": hashlib.sha256(
                        config.api_url.encode("utf-8")
                    ).hexdigest(),
                    "apiModel": config.api_model,
                    "llmEndpointHash": hashlib.sha256(
                        config.llm_url.encode("utf-8")
                    ).hexdigest(),
                    "llmModel": config.llm_model,
                    "allowLlmFallback": config.allow_llm_fallback,
                }
            ),
        }

    @staticmethod
    def _case_report(
        *,
        case: dict[str, Any],
        lexical_ids: list[str],
        proposed_ids: list[str],
        status: str,
        fallback_reason: str | None,
        duration_ms: int,
    ) -> dict[str, Any]:
        judgments = {
            row["candidateId"]: int(row["relevance"])
            for row in case.get("judgments", [])
        }

        def metrics(ranks: list[str]) -> dict[str, float]:
            top_6 = ranks[:6]
            relevant = [
                rank
                for rank, candidate_id in enumerate(ranks[:24], start=1)
                if judgments.get(candidate_id, 0) > 0
            ]
            grades = [judgments.get(candidate_id, 0) for candidate_id in top_6]
            dcg = sum(
                (2**grade - 1) / math.log2(rank + 1)
                for rank, grade in enumerate(grades, start=1)
                if grade > 0
            )
            ideal = sorted(
                (grade for grade in judgments.values() if grade > 0), reverse=True
            )[:6]
            ideal_dcg = sum(
                (2**grade - 1) / math.log2(rank + 1)
                for rank, grade in enumerate(ideal, start=1)
            )
            return {
                "recallAt24": 1.0 if relevant else 0.0,
                "mrrAt6": 1.0 / relevant[0] if relevant and relevant[0] <= 6 else 0.0,
                "nDCGAt6": dcg / ideal_dcg if ideal_dcg else 0.0,
                "top1": 1.0 if top_6 and judgments.get(top_6[0], 0) > 0 else 0.0,
                "nearMissFalsePositive": 1.0 if case["kind"] == "near_miss" and top_6 else 0.0,
            }

        return {
            "caseId": case["caseId"],
            "kind": case["kind"],
            "scope": case["scope"],
            "status": status,
            "fallbackReason": fallback_reason,
            "durationMs": duration_ms,
            "lexical": metrics(lexical_ids),
            "semantic": metrics(proposed_ids),
            "rankChanges": sum(
                1
                for candidate_id in lexical_ids
                if lexical_ids.index(candidate_id) != proposed_ids.index(candidate_id)
            ),
        }

    @staticmethod
    def _aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
        positive = [row for row in reports if row["kind"] == "positive"]
        near_miss = [row for row in reports if row["kind"] == "near_miss"]

        def mean(rows: list[dict[str, Any]], section: str, key: str) -> float:
            return round(
                sum(float(row[section][key]) for row in rows) / len(rows), 6
            ) if rows else 0.0

        return {
            "caseCount": len(reports),
            "positiveCaseCount": len(positive),
            "nearMissCaseCount": len(near_miss),
            "recallAt24": mean(positive, "semantic", "recallAt24"),
            "mrrAt6": mean(positive, "semantic", "mrrAt6"),
            "nDCGAt6": mean(positive, "semantic", "nDCGAt6"),
            "top1": mean(positive, "semantic", "top1"),
            "nearMissFalsePositiveRate": mean(
                near_miss, "semantic", "nearMissFalsePositive"
            ),
            "policyViolationCount": 0,
            "fallbackCount": sum(1 for row in reports if row["fallbackReason"]),
        }

    @staticmethod
    def _promotion_gates(
        *, baseline: dict[str, Any], semantic: dict[str, Any], case_count: int
    ) -> list[dict[str, Any]]:
        mrr_delta = round(
            float(semantic["mrrAt6"]) - float(baseline["mrrAt6"]), 6
        )
        ndcg_delta = round(
            float(semantic["nDCGAt6"]) - float(baseline["nDCGAt6"]), 6
        )
        recall_delta = round(
            float(semantic["recallAt24"]) - float(baseline["recallAt24"]), 6
        )
        near_miss_delta = round(
            float(semantic["nearMissFalsePositiveRate"])
            - float(baseline["nearMissFalsePositiveRate"]),
            6,
        )
        gates = [
            ("gold_cases_complete", case_count == int(baseline["caseCount"])),
            ("recall_at_24_preserved", recall_delta >= 0),
            ("policy_violations_zero", semantic["policyViolationCount"] == 0),
            ("provider_success_rate", semantic["providerSuccessRate"] >= 0.95),
            ("p95_latency", semantic["p95DurationMs"] <= 3_000),
            ("mrr_regression", mrr_delta >= -0.01),
            ("ndcg_regression", ndcg_delta >= -0.01),
            ("meaningful_improvement", max(mrr_delta, ndcg_delta) >= 0.03),
            ("near_miss_not_worse", near_miss_delta <= 0),
            ("provider_identity_stable", len(semantic["providerIdentities"]) == 1),
        ]
        return [
            {
                "code": code,
                "passed": bool(passed),
                "details": {
                    "mrrDelta": round(mrr_delta, 6),
                    "nDCGDelta": round(ndcg_delta, 6),
                    "recallDelta": round(recall_delta, 6),
                    "nearMissDelta": round(near_miss_delta, 6),
                },
            }
            for code, passed in gates
        ]

    def _feedback_summary(self) -> dict[str, Any]:
        feedback = self.store.list_feedback(limit=MAX_FEEDBACK_RECORDS)
        relevant = [row for row in feedback if row["judgment"] == "relevant"]
        not_relevant = [row for row in feedback if row["judgment"] == "not_relevant"]
        improved_relevant = sum(
            1
            for row in relevant
            if row.get("semanticRank") is not None
            and row.get("lexicalRank") is not None
            and row["semanticRank"] <= row["lexicalRank"]
        )
        demoted_irrelevant = sum(
            1
            for row in not_relevant
            if row.get("semanticRank") is not None
            and row.get("lexicalRank") is not None
            and row["semanticRank"] >= row["lexicalRank"]
        )
        return {
            "sampleCount": len(feedback),
            "relevantCount": len(relevant),
            "notRelevantCount": len(not_relevant),
            "relevantNonWorseCount": improved_relevant,
            "irrelevantNonWorseCount": demoted_irrelevant,
        }


__all__ = [
    "GOVERNANCE_VERSION",
    "SkillRerankGovernanceConflict",
    "SkillRerankGovernanceError",
    "SkillRerankGovernanceService",
    "SkillRerankGovernanceStore",
    "SkillRerankGovernanceUnavailable",
]
