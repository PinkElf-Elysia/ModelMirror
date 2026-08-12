from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Iterable


RUNTIME_INDEX_VERSION = 2
RANKER_VERSION = "skill-need-local-v3"
MAX_QUERY_LENGTH = 500
MAX_RESULTS = 6


class SkillFinderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "skill_finder_error") -> None:
        super().__init__(message)
        self.code = code


class SkillRuntimeIndexError(SkillFinderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="skill_runtime_index_invalid")


FIELD_DETAILS: tuple[tuple[str, str, float, tuple[str, ...]], ...] = (
    ("name", "名称", 12.0, ("name",)),
    ("included-skill", "子技能", 11.0, ("includedSkills",)),
    ("tag", "标签", 10.0, ("tags",)),
    ("description", "能力说明", 7.0, ("description",)),
    ("category", "分类", 5.0, ("category",)),
    ("path", "来源路径", 4.0, ("pathTerms",)),
    ("parent", "所属集合", 2.5, ("parentNames",)),
    (
        "source",
        "来源说明",
        3.0,
        ("searchDescription", "publisher", "sourceGroup"),
    ),
)

CJK_STOP_NGRAMS = {
    "一个",
    "一些",
    "可以",
    "如何",
    "希望",
    "需要",
    "我要",
    "我想",
    "帮我",
    "能够",
    "完成",
    "进行",
}


INTENT_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "label": "PDF 文档",
        "trigger": re.compile(r"\bpdf\b|便携式文档|合同.*(?:提取|填写|分析)|(?:提取|填写|分析).*合同", re.I),
        "terms": ("pdf", "文档", "合同", "表单", "提取"),
    },
    {
        "label": "电子表格",
        "trigger": re.compile(r"\bxlsx\b|\bexcel\b|spreadsheet|电子表格|工作簿|表格分析", re.I),
        "terms": ("xlsx", "excel", "spreadsheet", "电子表格", "工作簿", "表格", "csv"),
    },
    {
        "label": "网页自动化测试",
        "trigger": re.compile(r"playwright|cypress|selenium|\be2e\b|(?:网页|网站|web).*(?:测试|自动化)|(?:测试|自动化).*(?:网页|网站|web)", re.I),
        "terms": ("playwright", "cypress", "selenium", "e2e", "webapp testing", "browser testing", "网页测试", "自动化测试", "testing", "测试"),
        "required_any": ("playwright", "cypress", "selenium", "e2e", "webapp testing", "browser testing", "网页测试", "自动化测试", "testing", "test", "测试"),
    },
    {
        "label": "前端开发",
        "trigger": re.compile(r"react|next\.js|vue|svelte|tailwind|前端|网页界面|用户界面|\bui\b", re.I),
        "terms": ("react", "next.js", "vue", "svelte", "tailwind", "frontend", "前端", "界面", "ui"),
    },
    {
        "label": "数据库",
        "trigger": re.compile(r"postgres|postgresql|mysql|sqlite|mongodb|redis|supabase|数据库|\bsql\b", re.I),
        "terms": ("postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "supabase", "database", "数据库", "sql"),
    },
    {
        "label": "安全审计",
        "trigger": re.compile(r"安全|审计|漏洞|渗透|合规|security|secure|audit|vulnerab|pentest|compliance", re.I),
        "terms": ("安全", "审计", "漏洞", "合规", "security", "secure", "audit", "vulnerability", "pentest"),
    },
    {
        "label": "数据分析",
        "trigger": re.compile(r"数据分析|指标|可视化|统计|预测|analytics|analysis|metric|visuali[sz]|statistics|forecast", re.I),
        "terms": ("数据", "分析", "指标", "可视化", "analytics", "analysis", "metric", "visualization", "statistics"),
    },
    {
        "label": "研究",
        "trigger": re.compile(r"研究|论文|证据|文献|调研|research|paper|evidence|literature", re.I),
        "terms": ("研究", "论文", "证据", "文献", "research", "paper", "evidence"),
    },
    {
        "label": "营销增长",
        "trigger": re.compile(r"营销|推广|增长|广告|社交媒体|seo|marketing|campaign|growth|advertis", re.I),
        "terms": ("营销", "推广", "增长", "广告", "seo", "marketing", "campaign", "growth", "social"),
    },
    {
        "label": "产品与项目",
        "trigger": re.compile(r"产品|项目|需求|路线图|prd|roadmap|product|project|requirements", re.I),
        "terms": ("产品", "项目", "需求", "路线图", "prd", "roadmap", "product", "project", "requirements"),
    },
    {
        "label": "自动化与集成",
        "trigger": re.compile(r"自动化|工作流|集成|爬取|automation|workflow|integration|scraping|webhook|\bn8n\b", re.I),
        "terms": ("自动化", "工作流", "集成", "automation", "workflow", "integration", "scraping", "webhook", "n8n"),
    },
    {
        "label": "智能体与 MCP",
        "trigger": re.compile(r"智能体|提示词|知识检索|agent|prompt|\brag\b|\bmcp\b|\bllm\b", re.I),
        "terms": ("智能体", "提示词", "agent", "prompt", "rag", "mcp", "llm"),
    },
    {
        "label": "部署与运维",
        "trigger": re.compile(r"部署|运维|容器|云服务|deploy|devops|docker|kubernetes|terraform|cloud", re.I),
        "terms": ("部署", "运维", "容器", "deploy", "devops", "docker", "kubernetes", "terraform", "cloud"),
    },
    {
        "label": "演示文稿",
        "trigger": re.compile(r"pptx|幻灯片|演示文稿|slides?|presentation", re.I),
        "terms": ("pptx", "幻灯片", "演示文稿", "slides", "presentation"),
    },
    {
        "label": "图像与设计",
        "trigger": re.compile(r"图像|图片|设计|海报|插画|figma|image|design|illustrat|creative", re.I),
        "terms": ("图像", "图片", "设计", "figma", "image", "design", "illustration", "creative"),
    },
    {
        "label": "视频与音频",
        "trigger": re.compile(r"视频|动画|音频|语音|音乐|video|animation|audio|voice|music", re.I),
        "terms": ("视频", "动画", "音频", "语音", "音乐", "video", "animation", "audio", "voice", "music"),
    },
    {
        "label": "移动应用",
        "trigger": re.compile(r"移动端|手机应用|ios|android|flutter|expo|react native|mobile", re.I),
        "terms": ("移动端", "ios", "android", "flutter", "expo", "react native", "mobile"),
    },
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"[_/\\-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9+#.\u3400-\u9fff]+", " ", normalized, flags=re.I)
    return re.sub(r"\s+", " ", normalized).strip()


def _source_key(repo_url: str, sub_path: str) -> str:
    return f"{repo_url.strip().lower().removesuffix('.git')}#{sub_path.strip().strip('/')}"


def _field_values(candidate: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = candidate.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return values


def _extract_query(need: str) -> dict[str, Any]:
    normalized = _normalize(str(need or "")[:MAX_QUERY_LENGTH])
    direct: set[str] = set()
    direct_ordered: list[str] = []
    for term in re.findall(r"[a-z0-9+#.]{2,}", normalized, flags=re.I):
        if term in {"the", "and", "for", "with", "this", "that"} or term in direct:
            continue
        direct.add(term)
        direct_ordered.append(term)
    for chunk in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
        for size in range(2, min(6, len(chunk)) + 1):
            for index in range(0, len(chunk) - size + 1):
                term = chunk[index : index + size]
                if term not in CJK_STOP_NGRAMS and term not in direct:
                    direct.add(term)
                    direct_ordered.append(term)
    active_intents = [group for group in INTENT_GROUPS if group["trigger"].search(normalized)]
    expanded: set[str] = set()
    expanded_ordered: list[str] = []
    for intent in active_intents:
        for raw_term in intent["terms"]:
            term = _normalize(raw_term)
            if term not in direct and term not in expanded:
                expanded.add(term)
                expanded_ordered.append(term)
    terms = [*direct_ordered, *expanded_ordered]
    return {
        "normalized": normalized,
        "direct": direct,
        "expanded": expanded,
        "terms": [term for term in terms if len(term) >= 2][:256],
        "active_intents": active_intents,
    }


class SkillFinder:
    def __init__(self, *, index_path: str | Path | None = None, skill_manager: Any = None) -> None:
        self.index_path = Path(
            index_path or Path(__file__).resolve().parent / "data" / "skill_runtime_index.json"
        )
        self.skill_manager = skill_manager
        self._index: dict[str, Any] | None = None
        self._catalog_by_id: dict[str, dict[str, Any]] = {}

    @property
    def fingerprint(self) -> str:
        return str(self._load_index()["fingerprint"])

    def _load_index(self) -> dict[str, Any]:
        if self._index is not None:
            return self._index
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillRuntimeIndexError("Skill runtime index is unavailable.") from exc
        if (
            payload.get("version") != RUNTIME_INDEX_VERSION
            or payload.get("rankerVersion") != RANKER_VERSION
            or not isinstance(payload.get("candidates"), list)
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("catalogFingerprint") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("trustIndexFingerprint") or ""))
            or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("fingerprint") or ""))
        ):
            raise SkillRuntimeIndexError("Skill runtime index version is invalid.")
        expected = _fingerprint(
            {
                "version": payload.get("version"),
                "rankerVersion": payload.get("rankerVersion"),
                "memberIndexFingerprint": payload.get("memberIndexFingerprint"),
                "catalogFingerprint": payload.get("catalogFingerprint"),
                "trustIndexFingerprint": payload.get("trustIndexFingerprint"),
                "supersededCandidateIds": payload.get("supersededCandidateIds", []),
                "candidates": payload.get("candidates"),
            }
        )
        if expected != payload["fingerprint"]:
            raise SkillRuntimeIndexError("Skill runtime index fingerprint does not match its content.")
        catalog_by_id: dict[str, dict[str, Any]] = {}
        source_keys: set[str] = set()
        for candidate in payload["candidates"]:
            if not isinstance(candidate, dict):
                raise SkillRuntimeIndexError("Skill runtime index contains an invalid candidate.")
            candidate_id = str(candidate.get("candidateId") or "")
            source = candidate.get("installSource")
            trust = candidate.get("trust")
            if (
                not candidate_id.startswith("catalog:")
                or candidate_id in catalog_by_id
                or not isinstance(source, dict)
                or not isinstance(trust, dict)
                or not re.fullmatch(r"[0-9a-f]{40}", str(source.get("verifiedCommit") or ""))
                or not str(trust.get("receiptId") or "").startswith("skill-trust-")
                or not re.fullmatch(r"[0-9a-f]{64}", str(trust.get("trustFingerprint") or ""))
                or trust.get("riskLevel") not in {"low", "medium", "high", "critical"}
                or trust.get("trustStatus") not in {"verified", "conditional", "blocked"}
                or trust.get("installPolicy") not in {"allow", "confirm", "block"}
                or trust.get("compatibilityStatus") not in {"portable", "conditional", "unsupported"}
                or not isinstance(trust.get("routerEligible"), bool)
            ):
                raise SkillRuntimeIndexError("Skill runtime index contains an invalid install source.")
            candidate_payload = {
                key: value
                for key, value in candidate.items()
                if key not in {"candidateFingerprint", "stableNameOrder"}
            }
            if _fingerprint(candidate_payload) != candidate.get("candidateFingerprint"):
                raise SkillRuntimeIndexError("Skill candidate fingerprint is invalid.")
            key = _source_key(str(source.get("repoUrl") or ""), str(source.get("subPath") or ""))
            if key in source_keys:
                raise SkillRuntimeIndexError("Skill runtime index contains a duplicate source mapping.")
            source_keys.add(key)
            catalog_by_id[candidate_id] = candidate
        self._catalog_by_id = catalog_by_id
        self._index = payload
        return payload

    def _installed_candidates(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        installed = list(self.skill_manager.list_installed_skills()) if self.skill_manager else []
        by_source: dict[str, Any] = {}
        for skill in installed:
            by_source[_source_key(skill.repo_url, skill.sub_path)] = skill
        catalog_sources = {
            _source_key(
                str(candidate["installSource"]["repoUrl"]),
                str(candidate["installSource"]["subPath"]),
            )
            for candidate in self._load_index()["candidates"]
        }
        dynamic: list[dict[str, Any]] = []
        for order, skill in enumerate(installed):
            if _source_key(skill.repo_url, skill.sub_path) in catalog_sources:
                continue
            trust = None
            if str(getattr(skill, "source_kind", "")) == "local_import":
                trust = {
                    "receiptId": getattr(skill, "trust_receipt_id", None),
                    "trustFingerprint": getattr(skill, "trust_fingerprint", None),
                    "riskLevel": getattr(skill, "trust_risk_level", None),
                    "trustStatus": getattr(skill, "trust_status", None),
                    "installPolicy": getattr(skill, "trust_install_policy", None),
                    "compatibilityStatus": getattr(
                        skill, "trust_compatibility_status", None
                    ),
                    "routerEligible": bool(
                        getattr(skill, "trust_router_eligible", False)
                    ),
                }
            payload = {
                "candidateId": f"installed:{skill.skill_id}",
                "sourceType": "installed",
                "targetType": "installed",
                "sourceId": skill.skill_id,
                "name": skill.name,
                "category": "已安装 Skill",
                "kind": "skill",
                "description": skill.description,
                "sourceDescription": skill.description,
                "searchDescription": skill.description,
                "tags": [],
                "includedSkills": [],
                "pathTerms": [skill.sub_path, *re.split(r"[/_.-]+", skill.sub_path)],
                "parentNames": [],
                "publisher": "",
                "sourceGroup": "已安装",
                "parentSkillSets": [],
                "installSource": None,
                "installedSkillId": skill.skill_id,
                "installedSource": {
                    "repoUrl": skill.repo_url,
                    "subPath": skill.sub_path,
                    "sourceRef": skill.source_ref,
                    "installedAt": skill.installed_at,
                    "sourceKind": skill.source_kind,
                },
            }
            if trust is not None:
                payload["trust"] = trust
            dynamic.append(
                {
                    **payload,
                    "candidateFingerprint": _fingerprint(payload),
                    "stableNameOrder": len(self._catalog_by_id) + order,
                }
            )
        return dynamic, by_source

    def candidates(self) -> list[dict[str, Any]]:
        dynamic, _ = self._installed_candidates()
        return [*self._load_index()["candidates"], *dynamic]

    def resolve(self, candidate_id: str, candidate_fingerprint: str) -> dict[str, Any]:
        self._load_index()
        candidate = self._catalog_by_id.get(str(candidate_id))
        if candidate is None and str(candidate_id).startswith("installed:"):
            candidate = next(
                (
                    item
                    for item in self._installed_candidates()[0]
                    if item["candidateId"] == candidate_id
                ),
                None,
            )
        if candidate is None or candidate.get("candidateFingerprint") != candidate_fingerprint:
            raise SkillFinderError(
                "Skill candidate changed. Run skill_find again.",
                code="skill_candidate_stale",
            )
        return candidate

    def resolve_with_status(
        self,
        candidate_id: str,
        candidate_fingerprint: str,
        *,
        active_skill_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        candidate = self.resolve(candidate_id, candidate_fingerprint)
        active = {str(skill_id) for skill_id in active_skill_ids}
        _, installed_by_source = self._installed_candidates()
        installed_skill = None
        availability = "missing"
        if candidate["sourceType"] == "installed":
            installed_skill = next(
                (
                    skill
                    for skill in installed_by_source.values()
                    if skill.skill_id == candidate["installedSkillId"]
                ),
                None,
            )
            if installed_skill is None:
                raise SkillFinderError(
                    "Installed Skill changed. Run skill_find again.",
                    code="skill_candidate_stale",
                )
            availability = "active" if installed_skill.skill_id in active else "installed"
        else:
            source = candidate["installSource"]
            installed_skill = installed_by_source.get(
                _source_key(source["repoUrl"], source["subPath"])
            )
            if installed_skill is not None:
                if installed_skill.skill_id in active:
                    availability = "active"
                elif (installed_skill.source_ref or "").lower() == source["verifiedCommit"]:
                    availability = "installed"
                else:
                    availability = "stale"
        return {
            **candidate,
            "availability": availability,
            "installedSkillId": installed_skill.skill_id if installed_skill else None,
            "installedSourceRef": installed_skill.source_ref if installed_skill else None,
        }

    def find(
        self,
        need: str,
        *,
        limit: int = MAX_RESULTS,
        active_skill_ids: Iterable[str] = (),
        router_eligible_only: bool = False,
    ) -> dict[str, Any]:
        query = _extract_query(need)
        safe_limit = max(1, min(int(limit), MAX_RESULTS))
        if not query["normalized"] or not query["terms"]:
            return {
                "version": RUNTIME_INDEX_VERSION,
                "rankerVersion": RANKER_VERSION,
                "catalogFingerprint": self.fingerprint,
                "trustCatalogFingerprint": self._load_index()["catalogFingerprint"],
                "results": [],
            }
        candidates = self.candidates()
        if router_eligible_only:
            candidates = [
                candidate
                for candidate in candidates
                if (
                    candidate.get("sourceType") == "catalog"
                    and bool((candidate.get("trust") or {}).get("routerEligible"))
                )
                or (
                    candidate.get("sourceType") == "installed"
                    and (
                        (
                            str(
                                ((candidate.get("installedSource") or {}).get("sourceKind"))
                                or "git"
                            )
                            == "local_import"
                            and bool((candidate.get("trust") or {}).get("routerEligible"))
                        )
                        or str(
                            ((candidate.get("installedSource") or {}).get("sourceKind"))
                            or "git"
                        )
                        in {"workspace_draft", "plugin"}
                    )
                )
            ]
        prepared: list[dict[str, Any]] = []
        for candidate in candidates:
            fields = []
            for field_type, label, weight, keys in FIELD_DETAILS:
                searchable = _normalize(" ".join(_field_values(candidate, keys)))
                fields.append((field_type, label, weight, searchable))
            prepared.append(
                {
                    "candidate": candidate,
                    "fields": fields,
                    "text": _normalize(" ".join(field[3] for field in fields)),
                }
            )
        idf: dict[str, float] = {}
        for term in query["terms"]:
            count = sum(term in item["text"] for item in prepared)
            idf[term] = math.log((len(prepared) + 1) / (count + 1)) + 1

        active = {str(skill_id) for skill_id in active_skill_ids}
        _, installed_by_source = self._installed_candidates()
        matches: list[dict[str, Any]] = []
        for item in prepared:
            if any(
                intent.get("required_any")
                and not any(_normalize(term) in item["text"] for term in intent["required_any"])
                for intent in query["active_intents"]
            ):
                continue
            score = 0.0
            reasons: list[dict[str, Any]] = []
            for field_type, label, weight, searchable in item["fields"]:
                if not searchable:
                    continue
                matched = sorted(
                    {term for term in query["terms"] if term in searchable},
                    key=lambda term: (term not in query["direct"], -len(term), term),
                )[:5]
                if not matched:
                    continue
                field_score = sum(
                    idf.get(term, 1.0)
                    * (1.25 if term in query["direct"] else 0.7)
                    * (1 + min(len(term), 10) / 20)
                    for term in matched
                )
                score += weight * field_score
                if len(query["normalized"]) >= 3 and query["normalized"] in searchable:
                    score += weight * 1.5
                direct = any(term in query["direct"] for term in matched)
                reasons.append(
                    {
                        "type": field_type,
                        "label": f"{label}{'直接匹配' if direct else '关联匹配'}",
                        "origin": "direct" if direct else "expanded",
                        "matchedTerms": matched[:4],
                    }
                )
            if score < 6 or not reasons:
                continue
            candidate = item["candidate"]
            installed_skill = None
            availability = "missing"
            if candidate["sourceType"] == "installed":
                installed_skill = next(
                    (
                        skill
                        for skill in installed_by_source.values()
                        if skill.skill_id == candidate["installedSkillId"]
                    ),
                    None,
                )
                availability = "active" if candidate["installedSkillId"] in active else "installed"
            else:
                source = candidate["installSource"]
                installed_skill = installed_by_source.get(
                    _source_key(source["repoUrl"], source["subPath"])
                )
                if installed_skill is not None:
                    if installed_skill.skill_id in active:
                        availability = "active"
                    elif (installed_skill.source_ref or "").lower() == source["verifiedCommit"]:
                        availability = "installed"
                    else:
                        availability = "stale"
            matches.append(
                {
                    "candidateId": candidate["candidateId"],
                    "candidateFingerprint": candidate["candidateFingerprint"],
                    "sourceType": candidate["sourceType"],
                    "name": candidate["name"],
                    "summary": candidate["description"],
                    "category": candidate["category"],
                    "sourceDescription": candidate.get("sourceDescription") or "",
                    "parentNames": candidate.get("parentNames") or [],
                    "installSource": candidate.get("installSource"),
                    "trust": candidate.get("trust"),
                    "installedSkillId": installed_skill.skill_id if installed_skill else None,
                    "installedSourceRef": installed_skill.source_ref if installed_skill else None,
                    "availability": availability,
                    "score": round(score, 2),
                    "reasons": sorted(
                        reasons,
                        key=lambda reason: (
                            reason["origin"] != "direct",
                            next(
                                index
                                for index, detail in enumerate(FIELD_DETAILS)
                                if detail[0] == reason["type"]
                            ),
                        ),
                    ),
                    "stableNameOrder": candidate.get("stableNameOrder", 0),
                }
            )
        availability_boost = {"active": 0.5, "installed": 0.4, "stale": 0.2, "missing": 0.0}

        def compare_matches(left: dict[str, Any], right: dict[str, Any]) -> int:
            adjusted_left = float(left["score"]) + availability_boost.get(
                left["availability"], 0.0
            )
            adjusted_right = float(right["score"]) + availability_boost.get(
                right["availability"], 0.0
            )
            if adjusted_left != adjusted_right:
                return -1 if adjusted_left > adjusted_right else 1
            order_difference = int(left["stableNameOrder"]) - int(
                right["stableNameOrder"]
            )
            if order_difference:
                return order_difference
            return (left["candidateId"] > right["candidateId"]) - (
                left["candidateId"] < right["candidateId"]
            )

        matches.sort(key=cmp_to_key(compare_matches))
        for match in matches:
            match.pop("stableNameOrder", None)
        return {
            "version": RUNTIME_INDEX_VERSION,
            "rankerVersion": RANKER_VERSION,
            "catalogFingerprint": self.fingerprint,
            "trustCatalogFingerprint": self._load_index()["catalogFingerprint"],
            "results": matches[:safe_limit],
        }


__all__ = [
    "MAX_RESULTS",
    "RANKER_VERSION",
    "SkillFinder",
    "SkillFinderError",
    "SkillRuntimeIndexError",
]
