from __future__ import annotations

from typing import Any


SOURCE = "ModelMirror-authored bilingual synthetic benchmark; no external dataset."
LICENSE = "LicenseRef-ModelMirror-Project"


def _exact_case(
    case_id: str,
    locale: str,
    name: str,
    message: str,
    answer: str,
    *tags: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "message": message,
        "tags": [locale, *tags],
        "expected": {"exact_answer": answer},
        "weights": {"exact_match": 1.0},
    }


def _schema_case(
    case_id: str,
    locale: str,
    name: str,
    message: str,
    properties: dict[str, Any],
    *tags: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "message": message,
        "tags": [locale, *tags],
        "expected": {
            "json_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
        },
        "weights": {"json_schema": 1.0},
    }


def _contains_case(
    case_id: str,
    locale: str,
    name: str,
    message: str,
    fragments: list[str],
    *tags: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "message": message,
        "tags": [locale, *tags],
        "expected": {"contains": fragments},
        "weights": {"contains": 1.0},
    }


def _history_case(
    case_id: str,
    locale: str,
    name: str,
    messages: list[dict[str, str]],
    question: str,
    answer: str,
    *tags: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "message": question,
        "messages": messages,
        "tags": [locale, *tags],
        "expected": {"exact_answer": answer},
        "weights": {"exact_match": 1.0},
    }


def _instruction_following_cases() -> list[dict[str, Any]]:
    return [
        _exact_case("if-zh-01", "zh-CN", "固定短语", "只输出：晨星。不要添加标点或解释。", "晨星", "literal"),
        _exact_case("if-zh-02", "zh-CN", "大写转换", "将 mm-42 转为大写，只输出转换结果。", "MM-42", "transform"),
        _exact_case("if-zh-03", "zh-CN", "数字排序", "将 7、2、9、2 升序排列，用英文逗号连接且不要空格。", "2,2,7,9", "ordering"),
        _contains_case("if-zh-04", "zh-CN", "必含术语", "用一句简短的话描述评测流程，必须同时包含“输入”和“输出”。", ["输入", "输出"], "required_terms"),
        _exact_case("if-zh-05", "zh-CN", "标签包裹", "把词语蓝图放在一对方括号中，只输出结果。", "[蓝图]", "format"),
        _exact_case("if-zh-06", "zh-CN", "布尔判断", "若 18 能被 3 整除，只输出 true，否则只输出 false。", "true", "reasoning"),
        _exact_case("if-zh-07", "zh-CN", "取首字母", "取“模型 镜像 评测”三个词的首字，连续输出且不加空格。", "模镜评", "transform"),
        _exact_case("if-zh-08", "zh-CN", "去重保序", "对 A,B,A,C,B 去重并保留首次出现顺序，用逗号连接。", "A,B,C", "ordering"),
        _exact_case("if-zh-09", "zh-CN", "算术约束", "计算 (12-5)*3，只输出十进制整数。", "21", "reasoning"),
        _exact_case("if-zh-10", "zh-CN", "禁止前后缀", "回答代号 K9；不得出现“答案”、冒号、句号或代码块。", "K9", "negative_constraint"),
        _exact_case("if-en-01", "en", "Literal phrase", "Output exactly: north-star. Add no punctuation or explanation.", "north-star", "literal"),
        _exact_case("if-en-02", "en", "Lowercase conversion", "Convert DELTA_7 to lowercase and output only the result.", "delta_7", "transform"),
        _exact_case("if-en-03", "en", "Numeric ordering", "Sort 8, 1, 5, 1 ascending. Join with commas and no spaces.", "1,1,5,8", "ordering"),
        _contains_case("if-en-04", "en", "Required terms", "Write one short sentence about evaluation that includes both evidence and review.", ["evidence", "review"], "required_terms"),
        _exact_case("if-en-05", "en", "Wrapper format", "Wrap the word verified in one pair of angle brackets. Output only the result.", "<verified>", "format"),
        _exact_case("if-en-06", "en", "Boolean decision", "If 25 is divisible by 5 output only true; otherwise output only false.", "true", "reasoning"),
        _exact_case("if-en-07", "en", "Initial letters", "Take the initials of Model Mirror Benchmark and output them without spaces.", "MMB", "transform"),
        _exact_case("if-en-08", "en", "Stable deduplication", "Deduplicate red,blue,red,green,blue while preserving first occurrence. Join with commas.", "red,blue,green", "ordering"),
        _exact_case("if-en-09", "en", "Arithmetic constraint", "Calculate (15-6)*4 and output only the decimal integer.", "36", "reasoning"),
        _exact_case("if-en-10", "en", "Forbidden prefix", "Return code Q7. Do not include labels, colons, punctuation, or a code fence.", "Q7", "negative_constraint"),
    ]


def _structured_output_cases() -> list[dict[str, Any]]:
    return [
        _schema_case("json-zh-01", "zh-CN", "状态对象", "只输出 JSON 对象：status 为 ready，count 为 3。", {"status": {"const": "ready"}, "count": {"const": 3}}, "flat"),
        _schema_case("json-zh-02", "zh-CN", "布尔对象", "只输出 JSON：enabled 为 true，mode 为 safe。", {"enabled": {"const": True}, "mode": {"const": "safe"}}, "boolean"),
        _schema_case("json-zh-03", "zh-CN", "数组对象", "只输出 JSON，items 必须依次为甲、乙、丙。", {"items": {"const": ["甲", "乙", "丙"]}}, "array"),
        _schema_case("json-zh-04", "zh-CN", "嵌套对象", "只输出 JSON，result 内含 code=200 与 ok=true。", {"result": {"const": {"code": 200, "ok": True}}}, "nested"),
        _schema_case("json-zh-05", "zh-CN", "空值对象", "只输出 JSON，value 为 null，reason 为 missing。", {"value": {"const": None}, "reason": {"const": "missing"}}, "null"),
        _schema_case("json-zh-06", "zh-CN", "数值对象", "只输出 JSON，total 为 12.5，currency 为 CNY。", {"total": {"const": 12.5}, "currency": {"const": "CNY"}}, "number"),
        _schema_case("json-zh-07", "zh-CN", "双层数组", "只输出 JSON，matrix 为 [[1,2],[3,4]]。", {"matrix": {"const": [[1, 2], [3, 4]]}}, "array", "nested"),
        _schema_case("json-zh-08", "zh-CN", " Unicode 对象", "只输出 JSON，language 为 zh-CN，text 为 模镜。", {"language": {"const": "zh-CN"}, "text": {"const": "模镜"}}, "unicode"),
        _schema_case("json-en-01", "en", "Status object", "Return only JSON with status=ready and count=3.", {"status": {"const": "ready"}, "count": {"const": 3}}, "flat"),
        _schema_case("json-en-02", "en", "Boolean object", "Return only JSON with enabled=false and mode=review.", {"enabled": {"const": False}, "mode": {"const": "review"}}, "boolean"),
        _schema_case("json-en-03", "en", "Array object", "Return only JSON whose items are alpha, beta, gamma in that order.", {"items": {"const": ["alpha", "beta", "gamma"]}}, "array"),
        _schema_case("json-en-04", "en", "Nested object", "Return only JSON with result containing code=201 and ok=true.", {"result": {"const": {"code": 201, "ok": True}}}, "nested"),
        _schema_case("json-en-05", "en", "Null object", "Return only JSON with value=null and reason=unknown.", {"value": {"const": None}, "reason": {"const": "unknown"}}, "null"),
        _schema_case("json-en-06", "en", "Numeric object", "Return only JSON with total=19.75 and currency=USD.", {"total": {"const": 19.75}, "currency": {"const": "USD"}}, "number"),
        _schema_case("json-en-07", "en", "Matrix object", "Return only JSON with matrix=[[2,4],[6,8]].", {"matrix": {"const": [[2, 4], [6, 8]]}}, "array", "nested"),
        _schema_case("json-en-08", "en", "Escaped text", "Return only JSON with label set to line-one and separator set to slash.", {"label": {"const": "line-one"}, "separator": {"const": "slash"}}, "string"),
    ]


def _multi_turn_cases() -> list[dict[str, Any]]:
    return [
        _history_case("ctx-zh-01", "zh-CN", "代号召回", [{"role": "user", "content": "本次任务代号是松针。"}, {"role": "assistant", "content": "已记录。"}], "只输出任务代号。", "松针", "recall"),
        _history_case("ctx-zh-02", "zh-CN", "更新值优先", [{"role": "user", "content": "首选颜色是红色。"}, {"role": "assistant", "content": "明白。"}, {"role": "user", "content": "更正：首选颜色改为蓝色。"}], "只输出当前首选颜色。", "蓝色", "conflict"),
        _history_case("ctx-zh-03", "zh-CN", "人物属性", [{"role": "user", "content": "林舟负责测试，周岚负责设计。"}, {"role": "assistant", "content": "职责已记录。"}], "只输出负责测试的人名。", "林舟", "entity"),
        _history_case("ctx-zh-04", "zh-CN", "数字召回", [{"role": "user", "content": "迭代预算是 48 点。"}, {"role": "assistant", "content": "收到。"}], "只输出预算数字。", "48", "recall"),
        _history_case("ctx-zh-05", "zh-CN", "否定更新", [{"role": "user", "content": "允许导出 PDF。"}, {"role": "assistant", "content": "已记录。"}, {"role": "user", "content": "最终决定：不要导出 PDF。"}], "若当前允许导出 PDF 只输出 yes，否则只输出 no。", "no", "conflict"),
        _history_case("ctx-zh-06", "zh-CN", "列表定位", [{"role": "user", "content": "顺序是采集、校验、发布。"}, {"role": "assistant", "content": "顺序已保存。"}], "只输出第二个步骤。", "校验", "ordering"),
        _history_case("ctx-zh-07", "zh-CN", "跨轮别名", [{"role": "user", "content": "把项目 Nebula 简称为 N7。"}, {"role": "assistant", "content": "后续将使用该简称。"}], "只输出 Nebula 的简称。", "N7", "alias"),
        _history_case("ctx-zh-08", "zh-CN", "干扰信息", [{"role": "user", "content": "测试环境端口是 7100，生产环境端口是 9100。"}, {"role": "assistant", "content": "两个端口已区分。"}], "只输出生产环境端口。", "9100", "selection"),
        _history_case("ctx-en-01", "en", "Code recall", [{"role": "user", "content": "The task codename is cedar."}, {"role": "assistant", "content": "Recorded."}], "Output only the task codename.", "cedar", "recall"),
        _history_case("ctx-en-02", "en", "Latest value wins", [{"role": "user", "content": "The preferred region is east."}, {"role": "assistant", "content": "Understood."}, {"role": "user", "content": "Correction: the preferred region is west."}], "Output only the current preferred region.", "west", "conflict"),
        _history_case("ctx-en-03", "en", "Owner lookup", [{"role": "user", "content": "Mira owns testing and Owen owns design."}, {"role": "assistant", "content": "The ownership is noted."}], "Output only the person who owns testing.", "Mira", "entity"),
        _history_case("ctx-en-04", "en", "Number recall", [{"role": "user", "content": "The iteration budget is 72 points."}, {"role": "assistant", "content": "Recorded."}], "Output only the budget number.", "72", "recall"),
        _history_case("ctx-en-05", "en", "Negated update", [{"role": "user", "content": "CSV export is allowed."}, {"role": "assistant", "content": "Noted."}, {"role": "user", "content": "Final decision: disable CSV export."}], "Output only yes if CSV export is currently allowed, otherwise output only no.", "no", "conflict"),
        _history_case("ctx-en-06", "en", "Sequence lookup", [{"role": "user", "content": "The order is collect, validate, publish."}, {"role": "assistant", "content": "Order saved."}], "Output only the second step.", "validate", "ordering"),
        _history_case("ctx-en-07", "en", "Alias recall", [{"role": "user", "content": "Use R4 as the short name for project River."}, {"role": "assistant", "content": "I will use that alias."}], "Output only River's short name.", "R4", "alias"),
        _history_case("ctx-en-08", "en", "Distractor selection", [{"role": "user", "content": "The staging port is 7300 and the production port is 9300."}, {"role": "assistant", "content": "The ports are distinguished."}], "Output only the production port.", "9300", "selection"),
    ]


def _abstention_cases() -> list[dict[str, Any]]:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "answer": {"type": "null"},
            "status": {"const": "insufficient_evidence"},
        },
        "required": ["answer", "status"],
        "additionalProperties": False,
    }
    specs = [
        ("abstain-zh-01", "zh-CN", "缺失日期", "已知资料只说明项目名为北辰。资料没有上线日期。询问：上线日期是什么？"),
        ("abstain-zh-02", "zh-CN", "缺失负责人", "证据仅包含预算 30 万元，没有负责人信息。询问：负责人是谁？"),
        ("abstain-zh-03", "zh-CN", "相邻事实不可推断", "资料说明 A 城气温 20 度，没有 B 城数据。询问：B 城气温是多少？"),
        ("abstain-zh-04", "zh-CN", "冲突无裁决", "来源一称版本为 4，来源二称版本为 5，没有时间或权威标识。询问：最终版本是多少？"),
        ("abstain-zh-05", "zh-CN", "缺失单位", "资料只记录数值 18，没有单位。询问：该长度是多少米？"),
        ("abstain-zh-06", "zh-CN", "未提供外部事实", "上下文没有任何关于星河公司的信息。询问：星河公司的 CEO 是谁？"),
        ("abstain-en-01", "en", "Missing date", "The evidence only names the project Aurora. It provides no launch date. Question: What is the launch date?"),
        ("abstain-en-02", "en", "Missing owner", "The evidence contains a budget of 40,000 but no owner. Question: Who owns the project?"),
        ("abstain-en-03", "en", "Unsupported neighbor fact", "The evidence gives City A a temperature of 20 C and has no data for City B. Question: What is City B's temperature?"),
        ("abstain-en-04", "en", "Unresolved conflict", "Source one says version 4 and source two says version 5. Neither has a date or authority marker. Question: What is the final version?"),
        ("abstain-en-05", "en", "Missing unit", "The evidence records only the value 18 and gives no unit. Question: What is the length in meters?"),
        ("abstain-en-06", "en", "Absent external fact", "The context contains no information about Northwind Labs. Question: Who is the CEO of Northwind Labs?"),
    ]
    return [
        {
            "case_id": case_id,
            "name": name,
            "message": (
                f"{message} 证据不足时只输出 JSON，answer 为 null，status 为 insufficient_evidence。"
                if locale == "zh-CN"
                else f"{message} When evidence is insufficient, return only JSON with answer null and status insufficient_evidence."
            ),
            "tags": [locale, "abstention", "insufficient_evidence"],
            "expected": {"json_schema": schema},
            "weights": {"json_schema": 1.0},
        }
        for case_id, locale, name, message in specs
    ]


def _manifest(
    pack_id: str,
    name: str,
    description: str,
    coverage: list[str],
    difficulty: str,
) -> dict[str, Any]:
    return {
        "pack_id": pack_id,
        "version": 1,
        "kind": "agent_response",
        "name": name,
        "description": description,
        "locales": ["zh-CN", "en"],
        "coverage": coverage,
        "difficulty": difficulty,
        "metric_policy": {
            "core": ["exact_match", "contains", "json_schema"],
            "llm_judge": "optional_only",
        },
        "target_requirements": {
            "runtime": "xpert_evaluator",
            "side_effects": False,
            "interactive_waits": False,
        },
        "source": SOURCE,
        "license": LICENSE,
    }


def builtin_pack_specs() -> list[dict[str, Any]]:
    return [
        {
            "manifest": _manifest(
                "mm-agent-instruction-bilingual-v1",
                "中英双语：指令与约束遵循",
                "20 个可重复样例，覆盖格式、长度、排序、转换和负向约束。",
                ["instruction_following", "format", "ordering", "negative_constraints"],
                "mixed",
            ),
            "cases": _instruction_following_cases(),
        },
        {
            "manifest": _manifest(
                "mm-agent-structured-json-bilingual-v1",
                "中英双语：JSON 与结构化输出",
                "16 个严格 JSON Schema 样例，覆盖对象、数组、嵌套、布尔和空值。",
                ["structured_output", "json_schema", "nested_data", "unicode"],
                "intermediate",
            ),
            "cases": _structured_output_cases(),
        },
        {
            "manifest": _manifest(
                "mm-agent-multiturn-bilingual-v1",
                "中英双语：多轮上下文与冲突处理",
                "16 个多轮样例，覆盖召回、更新优先、实体选择和干扰信息。",
                ["multi_turn", "context_recall", "conflict_resolution", "entity_selection"],
                "mixed",
            ),
            "cases": _multi_turn_cases(),
        },
        {
            "manifest": _manifest(
                "mm-agent-abstention-bilingual-v1",
                "中英双语：证据不足与可验证弃答",
                "12 个严格弃答样例，覆盖缺失、冲突、单位不足和外部事实缺口。",
                ["abstention", "insufficient_evidence", "conflicting_evidence"],
                "intermediate",
            ),
            "cases": _abstention_cases(),
        },
    ]
