from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .semantic_rerank import (
    SkillRerankRequest,
    SkillSearchIndexError,
    SkillSearchIndexV1,
    _fingerprint,
)


EVALUATION_VERSION = 1
EVALUATION_NAME = "skill-rerank-eval-v1"


@dataclass(frozen=True)
class SkillRerankEvaluationReport:
    evaluation_name: str
    evaluation_fingerprint: str
    search_index_fingerprint: str
    case_count: int
    positive_case_count: int
    near_miss_case_count: int
    recall_at_24: float
    mrr_at_6: float
    ndcg_at_6: float
    top_1: float
    near_miss_false_positive_rate: float
    policy_violation_count: int
    cases: tuple[dict[str, Any], ...]

    def serialize(self) -> dict[str, Any]:
        return {
            "evaluationName": self.evaluation_name,
            "evaluationFingerprint": self.evaluation_fingerprint,
            "searchIndexFingerprint": self.search_index_fingerprint,
            "caseCount": self.case_count,
            "positiveCaseCount": self.positive_case_count,
            "nearMissCaseCount": self.near_miss_case_count,
            "recallAt24": self.recall_at_24,
            "mrrAt6": self.mrr_at_6,
            "nDCGAt6": self.ndcg_at_6,
            "top1": self.top_1,
            "nearMissFalsePositiveRate": self.near_miss_false_positive_rate,
            "policyViolationCount": self.policy_violation_count,
            "cases": list(self.cases),
        }


class SkillRerankEvaluator:
    def __init__(
        self,
        *,
        search_index: SkillSearchIndexV1 | None = None,
        evaluation_path: str | Path | None = None,
    ) -> None:
        self.search_index = search_index or SkillSearchIndexV1()
        self.evaluation_path = Path(
            evaluation_path
            or Path(__file__).resolve().parent / "data" / "skill_rerank_eval_v1.json"
        )

    def load_cases(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.evaluation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillSearchIndexError("Skill rerank evaluation set is unavailable.") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("version") != EVALUATION_VERSION
            or payload.get("name") != EVALUATION_NAME
            or payload.get("searchIndexFingerprint") != self.search_index.fingerprint
            or payload.get("directoryFingerprint")
            != self.search_index._load().get("directoryFingerprint")
            or _fingerprint(
                {key: value for key, value in payload.items() if key != "fingerprint"}
            )
            != payload.get("fingerprint")
            or not isinstance(payload.get("cases"), list)
        ):
            raise SkillSearchIndexError("Skill rerank evaluation set is stale or invalid.")
        case_ids: set[str] = set()
        positive_count = 0
        near_miss_count = 0
        all_candidates = {
            candidate["candidateId"] for candidate in self.search_index.candidates()
        }
        for case in payload["cases"]:
            if not isinstance(case, dict):
                raise SkillSearchIndexError("Skill rerank evaluation case is invalid.")
            case_id = str(case.get("caseId") or "")
            kind = case.get("kind")
            judgments = case.get("judgments")
            if (
                not case_id
                or case_id in case_ids
                or kind not in {"positive", "near_miss"}
                or case.get("scope") not in {"market", "router"}
                or not str(case.get("query") or "").strip()
                or not isinstance(judgments, list)
            ):
                raise SkillSearchIndexError("Skill rerank evaluation case is invalid.")
            case_ids.add(case_id)
            judged_ids: set[str] = set()
            for judgment in judgments:
                candidate_id = str((judgment or {}).get("candidateId") or "")
                relevance = (judgment or {}).get("relevance")
                if (
                    candidate_id not in all_candidates
                    or candidate_id in judged_ids
                    or not isinstance(relevance, int)
                    or relevance not in {0, 1, 2, 3}
                ):
                    raise SkillSearchIndexError(
                        "Skill rerank evaluation judgment is invalid."
                    )
                judged_ids.add(candidate_id)
            if kind == "positive":
                positive_count += 1
                if not any(judgment["relevance"] > 0 for judgment in judgments):
                    raise SkillSearchIndexError(
                        "Positive Skill rerank case must contain a relevant candidate."
                    )
            else:
                near_miss_count += 1
                if any(judgment["relevance"] > 0 for judgment in judgments):
                    raise SkillSearchIndexError(
                        "Near-miss Skill rerank case cannot contain a relevant candidate."
                    )
        if len(payload["cases"]) < 60 or positive_count < 40 or near_miss_count < 20:
            raise SkillSearchIndexError("Skill rerank evaluation coverage is incomplete.")
        return payload

    def evaluate(self) -> SkillRerankEvaluationReport:
        payload = self.load_cases()
        positive: list[dict[str, Any]] = []
        near_miss: list[dict[str, Any]] = []
        policy_violations = 0
        case_reports: list[dict[str, Any]] = []
        candidates_by_scope = {
            scope: {
                candidate["candidateId"]: candidate
                for candidate in self.search_index.candidates(scope=scope)
            }
            for scope in ("market", "router")
        }
        for case in payload["cases"]:
            outcome = self.search_index.lexical_search(
                SkillRerankRequest(
                    query=case["query"], scope=case["scope"], limit=6, semantic=False
                )
            )
            top_24 = [item["candidateId"] for item in outcome.lexical_results]
            top_6 = top_24[:6]
            judgments = {
                judgment["candidateId"]: judgment["relevance"]
                for judgment in case["judgments"]
            }
            relevant_ranks = [
                rank
                for rank, candidate_id in enumerate(top_24, start=1)
                if judgments.get(candidate_id, 0) > 0
            ]
            for item in outcome.lexical_results:
                candidate = candidates_by_scope[case["scope"]].get(item["candidateId"])
                if candidate is None:
                    policy_violations += 1
                elif case["scope"] == "router" and not candidate.get(
                    "runtimeCandidateFingerprint"
                ):
                    policy_violations += 1
            if case["kind"] == "positive":
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
                positive.append(
                    {
                        "recall": 1.0 if relevant_ranks else 0.0,
                        "reciprocalRank": (
                            1.0 / relevant_ranks[0]
                            if relevant_ranks and relevant_ranks[0] <= 6
                            else 0.0
                        ),
                        "ndcg": dcg / ideal_dcg if ideal_dcg else 0.0,
                        "top1": 1.0 if top_6 and judgments.get(top_6[0], 0) > 0 else 0.0,
                    }
                )
            else:
                near_miss.append({"falsePositive": 1.0 if top_6 else 0.0})
            case_reports.append(
                {
                    "caseId": case["caseId"],
                    "kind": case["kind"],
                    "scope": case["scope"],
                    "top24": top_24,
                    "relevantRanks": relevant_ranks,
                }
            )

        def mean(rows: list[dict[str, float]], key: str) -> float:
            return round(sum(row[key] for row in rows) / len(rows), 6) if rows else 0.0

        return SkillRerankEvaluationReport(
            evaluation_name=payload["name"],
            evaluation_fingerprint=payload["fingerprint"],
            search_index_fingerprint=self.search_index.fingerprint,
            case_count=len(payload["cases"]),
            positive_case_count=len(positive),
            near_miss_case_count=len(near_miss),
            recall_at_24=mean(positive, "recall"),
            mrr_at_6=mean(positive, "reciprocalRank"),
            ndcg_at_6=mean(positive, "ndcg"),
            top_1=mean(positive, "top1"),
            near_miss_false_positive_rate=mean(near_miss, "falsePositive"),
            policy_violation_count=policy_violations,
            cases=tuple(case_reports),
        )


__all__ = [
    "EVALUATION_NAME",
    "SkillRerankEvaluationReport",
    "SkillRerankEvaluator",
]
