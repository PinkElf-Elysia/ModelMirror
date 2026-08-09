from __future__ import annotations

from typing import Any


SOURCE = "ModelMirror-authored bilingual synthetic RAG benchmark; no external dataset."
LICENSE = "LicenseRef-ModelMirror-Project"


_DOCUMENT_SPECS: list[tuple[str, str, str, str, tuple[str, str, str]]] = [
    ("aurora_ops_zh", "aurora-operations-zh.md", "zh-CN", "极光设备运维手册", ("AUR-17 巡检每 6 小时执行一次。", "AUR-17 离线缓冲最多保留 48 小时。", "AUR-17 夜间升级须由值班负责人确认。")),
    ("orion_procurement_zh", "orion-procurement-zh.md", "zh-CN", "猎户采购控制规范", ("ORP-9 单笔超过 80000 元时需要三家有效报价。", "ORP-9 紧急采购须在两个工作日内补交说明。", "ORP-9 同一供应商连续三次中选后触发复核。")),
    ("helios_support_zh", "helios-support-zh.md", "zh-CN", "曦光支持响应标准", ("HLP-22 一级故障应在 15 分钟内响应。", "HLP-22 一级故障每 4 小时更新一次进展。", "HLP-22 恢复后 24 小时内提交复盘。")),
    ("nova_lab_zh", "nova-lab-zh.md", "zh-CN", "新星实验样本规范", ("NVR-31 样本应保存在零下 18 摄氏度。", "NVR-31 默认保存期限为 30 天。", "NVR-31 解冻后不得再次入库。")),
    ("atlas_travel_zh", "atlas-travel-zh.md", "zh-CN", "图谱差旅准则", ("ATL-5 单程四小时以内优先选择铁路。", "ATL-5 住宿上限为每晚 680 元。", "ATL-5 改签费用须附行程变更原因。")),
    ("cedar_security_zh", "cedar-security-zh.md", "zh-CN", "雪松访问安全制度", ("CDR-12 权限复核周期为 90 天。", "CDR-12 离职账号应在 2 小时内停用。", "CDR-12 临时权限最长有效 7 天。")),
    ("meridian_ops_en", "meridian-operations-en.md", "en", "Meridian Operations Handbook", ("MDR-44 backups begin at 02:30 UTC.", "MDR-44 backup copies are retained for 21 days.", "MDR-44 restore drills run on the first Tuesday of each month.")),
    ("solstice_hr_en", "solstice-mentoring-en.md", "en", "Solstice Mentoring Policy", ("SOL-8 mentoring cycles last six weeks.", "SOL-8 mentors meet participants at least once per week.", "SOL-8 completion requires a written retrospective.")),
    ("kepler_finance_en", "kepler-finance-en.md", "en", "Kepler Variance Policy", ("KEP-27 variances above seven percent require an explanation.", "KEP-27 reviews use the approved quarterly baseline.", "KEP-27 exceptions require the finance lead's approval.")),
    ("lumen_quality_en", "lumen-quality-en.md", "en", "Lumen Quality Sampling", ("LUM-14 inspection samples contain 32 units.", "LUM-14 critical defects trigger immediate lot quarantine.", "LUM-14 records are retained for 18 months.")),
    ("pacifica_logistics_en", "pacifica-logistics-en.md", "en", "Pacifica Cold Chain Guide", ("PAC-6 shipments must remain between 2 and 8 degrees Celsius.", "PAC-6 temperature excursions trigger an alert after 10 minutes.", "PAC-6 receiving teams archive the sensor report.")),
    ("veridian_research_en", "veridian-research-en.md", "en", "Veridian Review Protocol", ("VER-19 studies require two independent reviewers.", "VER-19 disagreements are resolved by a third reviewer.", "VER-19 review notes are retained with the study record.")),
]


def _document(spec: tuple[str, str, str, str, tuple[str, str, str]]) -> dict[str, Any]:
    key, filename, locale, title, anchors = spec
    sections = ("Core rule", "Procedure", "Exception") if locale == "en" else ("核心规则", "执行流程", "例外处理")
    content = f"# {title}\n\n" + "\n\n".join(
        f"## {section}\n\n{sentence}" for section, sentence in zip(sections, anchors, strict=True)
    ) + "\n"
    return {
        "document_key": key,
        "filename": filename,
        "locale": locale,
        "title": title,
        "content": content,
        "anchors": {
            f"{key}_{index + 1}": sentence
            for index, sentence in enumerate(anchors)
        },
    }


def _reference(document_key: str, anchor_index: int, relevance: int = 1) -> dict[str, Any]:
    return {
        "document_key": document_key,
        "anchor_key": f"{document_key}_{anchor_index}",
        "anchor_phrase": next(
            doc["anchors"][f"{document_key}_{anchor_index}"]
            for doc in [_document(spec) for spec in _DOCUMENT_SPECS]
            if doc["document_key"] == document_key
        ),
        "relevance": relevance,
    }


def _case(
    case_id: str,
    query: str,
    category: str,
    locale: str,
    references: list[tuple[str, int, int]],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "query": query,
        "expected_refs": [_reference(*reference) for reference in references],
        "expected_no_result": False,
        "tags": [category, locale],
        "notes": "ModelMirror synthetic deterministic Gold.",
    }


def _no_result(case_id: str, query: str, locale: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "query": query,
        "expected_refs": [],
        "expected_no_result": True,
        "tags": ["no_answer", locale],
        "notes": "The locked corpus intentionally contains no supporting statement.",
    }


def _cases() -> list[dict[str, Any]]:
    keys = [spec[0] for spec in _DOCUMENT_SPECS]
    factual_queries = [
        "AUR-17 的巡检间隔是多少？", "ORP-9 超过多少金额需要三家报价？", "HLP-22 一级故障应在多久内响应？",
        "NVR-31 样本的保存温度是多少？", "ATL-5 何种时长内优先选择铁路？", "CDR-12 权限多久复核一次？",
        "When does the MDR-44 backup start?", "How long is a SOL-8 mentoring cycle?", "What variance triggers a KEP-27 explanation?",
        "How many units are in a LUM-14 sample?", "What temperature range applies to PAC-6 shipments?", "How many reviewers does VER-19 require?",
    ]
    result = [
        _case(f"rag-fact-{index + 1:02d}", query, "fact", "zh-CN" if index < 6 else "en", [(key, 1, 3)])
        for index, (key, query) in enumerate(zip(keys, factual_queries, strict=True))
    ]
    paraphrases = [
        ("aurora_ops_zh", "设备失联后，AUR-17 最多能暂存两天数据吗？"),
        ("orion_procurement_zh", "ORP-9 的事后紧急采购说明最迟何时补齐？"),
        ("helios_support_zh", "严重故障处理中，HLP-22 要求多久同步一次进度？"),
        ("nova_lab_zh", "NVR-31 的常规样本可以留存一个月吗？"),
        ("atlas_travel_zh", "ATL-5 允许的单晚住宿费用上限是多少？"),
        ("cedar_security_zh", "CDR-12 要求离职账户多快失效？"),
        ("meridian_ops_en", "For how many weeks does MDR-44 keep a backup copy?"),
        ("solstice_hr_en", "Under SOL-8, how often must mentor and participant meet?"),
    ]
    result.extend(
        _case(f"rag-para-{index + 1:02d}", query, "paraphrase", "zh-CN" if index < 6 else "en", [(key, 2, 3)])
        for index, (key, query) in enumerate(paraphrases)
    )
    sections = [
        ("aurora_ops_zh", "概括 AUR-17 的离线保留时长与夜间升级条件。"),
        ("orion_procurement_zh", "说明 ORP-9 的紧急补交期限和连续中选复核条件。"),
        ("helios_support_zh", "HLP-22 要求多久更新进展，恢复后多久复盘？"),
        ("nova_lab_zh", "NVR-31 的保存期限和解冻后规则是什么？"),
        ("meridian_ops_en", "State MDR-44 retention and restore-drill timing."),
        ("solstice_hr_en", "State the SOL-8 meeting cadence and completion artifact."),
        ("kepler_finance_en", "Which KEP-27 baseline is used and who approves exceptions?"),
        ("pacifica_logistics_en", "When does PAC-6 alert, and what must receiving archive?"),
    ]
    result.extend(
        _case(f"rag-section-{index + 1:02d}", query, "parent_child", "zh-CN" if index < 4 else "en", [(key, 2, 2), (key, 3, 2)])
        for index, (key, query) in enumerate(sections)
    )
    cross_language = [
        ("aurora_ops_zh", "How often must AUR-17 inspections run?", 1),
        ("orion_procurement_zh", "What is the ORP-9 threshold for three quotations?", 1),
        ("helios_support_zh", "How quickly must an HLP-22 severity-one incident be acknowledged?", 1),
        ("meridian_ops_en", "MDR-44 的备份副本保留多少天？", 2),
        ("solstice_hr_en", "SOL-8 导师项目持续几周？", 1),
        ("kepler_finance_en", "KEP-27 在偏差超过多少时要求解释？", 1),
    ]
    result.extend(
        _case(f"rag-cross-{index + 1:02d}", query, "cross_language", "en" if index < 3 else "zh-CN", [(key, anchor, 3)])
        for index, (key, query, anchor) in enumerate(cross_language)
    )
    result.extend(
        [
            _no_result("rag-none-01", "ZEP-91 的量子电池保修期是多少？", "zh-CN"),
            _no_result("rag-none-02", "哪份制度规定了火星基地的氧气配额？", "zh-CN"),
            _no_result("rag-none-03", "RHO-55 是否允许无限期保存样本？", "zh-CN"),
            _no_result("rag-none-04", "What is the VTX-88 satellite launch window?", "en"),
            _no_result("rag-none-05", "Which policy defines an unlimited meal allowance?", "en"),
            _no_result("rag-none-06", "How many lunar vehicles does QRS-71 authorize?", "en"),
        ]
    )
    return result


def builtin_knowledge_pack_specs() -> list[dict[str, Any]]:
    return [
        {
            "manifest": {
                "pack_id": "modelmirror-rag-foundation-bilingual-v1",
                "version": 1,
                "kind": "knowledge_retrieval",
                "name": "ModelMirror 双语 RAG 引擎基础基准",
                "description": "用于检索引擎一致性与回归验证的锁定语料基准，不代表具体业务知识库质量。",
                "locales": ["zh-CN", "en"],
                "coverage": ["fact", "paraphrase", "parent_child", "cross_language", "no_answer"],
                "difficulty": "mixed",
                "metric_policy": {
                    "mode": "advisory",
                    "min_recall_at_5": 0.70,
                    "min_citation_coverage": 0.70,
                    "min_no_result_accuracy": 0.80,
                },
                "target_requirements": {
                    "index_schema_version": 2,
                    "processor_mode": "general",
                    "locked_corpus": True,
                },
                "source": SOURCE,
                "license": LICENSE,
            },
            "documents": [_document(spec) for spec in _DOCUMENT_SPECS],
            "cases": _cases(),
        }
    ]
