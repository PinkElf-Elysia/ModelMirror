"""Multilingual intent and task classification ported from OmniRoute.

Upstream:
  diegosouzapw/OmniRoute release/v3.8.49
  commit 36f8fd10052fd88f07e188b566f19a59c9cf5ea7
  open-sse/services/intentClassifier.ts
  open-sse/services/taskAwareRouting.ts

Copyright (c) 2026 diegosouzapw. Licensed under the MIT License.
Modified for ModelMirror: translated to Python, with typed structural inputs and
without storing or transmitting prompt text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


IntentType = Literal["code", "math", "reasoning", "creative", "simple", "medium"]
TaskLevel = Literal["light", "standard", "heavy", "critical"]

CODE_KEYWORDS = (
    "function", "class", "import", "def", "select", "async", "await", "const",
    "let", "var", "return", "```", "algorithm", "compile", "debug", "refactor",
    "typescript", "python", "javascript", "code", "implement", "write a",
    "create a component", "endpoint", "repository", "deploy", "install", "script",
    "api", "database", "query", "schema", "interface", "generic", "enum", "module",
    "package", "dependency", "função", "classe", "importar", "definir", "consulta",
    "assíncrono", "aguardar", "constante", "variável", "retornar", "algoritmo",
    "compilar", "depurar", "refatorar", "código", "implementar", "criar um",
    "componente", "como fazer", "repositório", "configurar", "instalar",
    "banco de dados", "escrever uma função", "criar uma classe", "función", "clase",
    "asíncrono", "esperar", "variable", "refactorizar", "函数", "类", "导入", "定义",
    "查询", "异步", "等待", "常量", "变量", "返回", "算法", "编译", "调试", "代码",
    "関数", "クラス", "インポート", "非同期", "定数", "変数", "コード",
    "アルゴリズム", "функция", "класс", "импорт", "запрос", "асинхронный",
    "константа", "переменная", "алгоритм", "код", "funktion", "klasse",
    "importieren", "abfrage", "asynchron", "konstante", "variable", "algorithmus",
    "함수", "클래스", "가져오기", "정의", "쿼리", "비동기", "대기", "상수", "변수",
    "반환", "코드", "دالة", "فئة", "استيراد", "استعلام", "غير متزامن", "ثابت",
    "متغير", "كود", "خوارزمية",
)

REASONING_KEYWORDS = (
    "prove", "theorem", "derive", "step by step", "chain of thought", "formally",
    "mathematical", "proof", "logically", "analyze", "reasoning", "deduce", "infer",
    "hypothesis", "convergence", "provar", "teorema", "derivar", "passo a passo",
    "cadeia de pensamento", "formalmente", "matemático", "prova", "logicamente",
    "analisar", "raciocínio", "deduzir", "inferir", "hipótese", "demonstrar",
    "cálculo", "equação diferencial", "integral", "otimização", "demostrar",
    "paso a paso", "lógicamente", "证明", "定理", "推导", "逐步", "思维链", "数学",
    "逻辑", "分析", "証明", "導出", "論理的", "доказать", "теорема",
    "шаг за шагом", "математически", "логически", "beweisen", "schritt für schritt",
    "mathematisch", "logisch", "증명", "정리", "단계별", "수학적", "논리적",
    "إثبات", "نظرية", "خطوة بخطوة", "رياضي", "منطقياً",
)

MATH_KEYWORDS = (
    "calculate", "solve", "equation", "proof", "formula", "integral", "derivative",
    "theorem", "algebra", "geometry", "arithmetic", "polynomial", "matrix", "vector",
    "statistics", "probability", "calcular", "resolver", "equação", "fórmula",
    "derivada", "álgebra", "geometria", "aritmética", "polinômio", "matriz", "vetor",
    "estatística", "probabilidade", "ecuación", "geometría", "polinomio", "vector",
    "estadística", "probabilidad", "计算", "求解", "方程", "公式", "积分", "导数",
    "代数", "几何", "算术", "多项式", "矩阵", "向量", "统计", "概率", "計算",
    "方程式", "公式", "積分", "微分", "幾何学", "算術", "多項式", "行列",
    "ベクトル", "統計", "確率", "вычислить", "решить", "уравнение", "формула",
    "интеграл", "производная", "алгебра", "геометрия", "арифметика", "полином",
    "матрица", "вектор", "статистика", "вероятность", "berechnen", "gleichung",
    "formel", "ableitung", "geometrie", "arithmetik", "polynom", "vektor", "statistik",
    "wahrscheinlichkeit", "계산", "방정식", "공식", "적분", "미분", "대수", "기하학",
    "산술", "다항식", "행렬", "벡터", "통계", "확률", "حل", "معادلة", "صيغة",
    "تكامل", "مشتق", "جبر", "هندسة", "حساب", "متعدد الحدود", "مصفوفة", "متجه",
    "إحصاء", "احتمال",
)

CREATIVE_KEYWORDS = (
    "write", "story", "poem", "creative", "brainstorm", "blog", "article", "copywrite",
    "marketing", "narrative", "fiction", "screenplay", "lyrics", "essay", "escrever",
    "história", "poema", "criativo", "artigo", "redação", "narrativa", "ficção",
    "roteiro", "letras", "ensaio", "escribir", "historia", "creativo", "artículo",
    "redacción", "ficción", "guion", "ensayo", "写", "故事", "诗", "创意", "头脑风暴",
    "博客", "文章", "文案", "营销", "叙事", "小说", "剧本", "歌词", "散文", "書く",
    "物語", "詩", "クリエイティブ", "ブログ", "記事", "コピーライティング",
    "マーケティング", "ナラティブ", "小説", "脚本", "歌詞", "エッセイ", "написать",
    "история", "стихотворение", "креативный", "статья", "копирайтинг", "маркетинг",
    "нарратив", "фантастика", "сценарий", "текст песни", "эссе", "schreiben",
    "geschichte", "gedicht", "kreativ", "artikel", "texten", "erzählung", "fiktion",
    "drehbuch", "songtext", "aufsatz", "쓰기", "이야기", "시", "창의적", "블로그",
    "기사", "카피라이팅", "마케팅", "서사", "소설", "시나리오", "가사", "에세이",
    "كتابة", "قصة", "قصيدة", "إبداعي", "مقال", "تسويق", "سرد", "رواية", "سيناريو",
    "كلمات أغنية", "مقالة",
)

SIMPLE_KEYWORDS = (
    "what is", "define", "translate", "hello", "yes or no", "summarize", "list",
    "tell me", "who is", "o que é", "definir", "traduzir", "olá", "oi", "sim ou não",
    "resumir", "listar", "me diga", "quem é", "quando foi", "onde fica",
    "explique brevemente", "de forma simples", "qué es", "traducir", "hola", "什么是",
    "定义", "翻译", "你好", "总结", "列出", "что такое", "определить", "перевести",
    "привет", "резюмировать", "was ist", "definieren", "übersetzen", "hallo",
    "zusammenfassen", "이란", "정의", "번역", "안녕", "요약", "ما هو", "تعريف",
    "ترجمة", "مرحبا", "ملخص",
)

LIGHT_TASK_RE = re.compile(
    r"\b(hi|hello|thanks|thank you|ping|format|rewrite|grammar|translate|"
    r"summari[sz]e|short|quick|one[- ]?liner|explain briefly)\b",
    re.IGNORECASE,
)
HEAVY_TASK_RE = re.compile(
    r"\b(debug|root cause|architecture|architectural|refactor|migrate|"
    r"implementation|implement|design|analy[sz]e|investigate|compare|benchmark|"
    r"whitebox|codebase|end[- ]?to[- ]?end|e2e)\b",
    re.IGNORECASE,
)
CRITICAL_TASK_RE = re.compile(
    r"\b(critical|security|vulnerability|exploit|rce|remote code execution|"
    r"supply chain|account takeover|auth bypass|privilege escalation|tenant|"
    r"cross[- ]tenant|sandbox escape|ssrf|deserialization|prod incident|"
    r"data exfiltration|bug bounty)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaskClassification:
    intent: IntentType
    level: TaskLevel
    reasons: tuple[str, ...]
    prompt_chars: int
    message_count: int
    tool_count: int
    output_tokens: int


def classify_prompt_intent(prompt: str, system_prompt: str = "") -> IntentType:
    """Match OmniRoute's priority: code > math > reasoning > creative > simple."""

    text = f"{system_prompt} {prompt}".lower()
    word_count = len(str(prompt or "").strip().split())
    for intent, keywords in (
        ("code", CODE_KEYWORDS),
        ("math", MATH_KEYWORDS),
        ("reasoning", REASONING_KEYWORDS),
        ("creative", CREATIVE_KEYWORDS),
    ):
        if any(keyword in text for keyword in keywords):
            return intent  # type: ignore[return-value]
    if word_count < 60 and any(keyword in text for keyword in SIMPLE_KEYWORDS):
        return "simple"
    return "medium"


def classify_task(
    prompt: str,
    *,
    message_count: int,
    tool_count: int,
    output_tokens: int,
    effort: str = "",
    system_prompt: str = "",
) -> TaskClassification:
    """Port the upstream light/standard/heavy/critical structural classifier."""

    text = f"{system_prompt} {prompt}"
    prompt_chars = len(text)
    normalized_effort = str(effort or "").lower()
    has_reasoning = normalized_effort not in {"", "none", "off", "disabled"}
    high_effort = bool(re.fullmatch(r"high|xhigh|max|maximum|hard|deep", normalized_effort))
    light_effort = not has_reasoning or bool(
        re.fullmatch(r"low|minimal|none|off|disabled", normalized_effort)
    )
    critical_keyword = bool(CRITICAL_TASK_RE.search(text))
    heavy_keyword = bool(HEAVY_TASK_RE.search(text))
    light_keyword = bool(LIGHT_TASK_RE.search(text))
    reasons: list[str] = []

    def add(condition: bool, reason: str) -> bool:
        if condition:
            reasons.append(reason)
        return condition

    critical = (
        add(prompt_chars >= 100_000, "huge-context")
        or add(output_tokens >= 32_768, "huge-output")
        or add(tool_count >= 8 and prompt_chars >= 16_000, "many-tools-large-context")
        or add(
            critical_keyword and (high_effort or tool_count >= 3 or prompt_chars >= 8_000),
            "critical-domain",
        )
    )
    if critical:
        level: TaskLevel = "critical"
    else:
        heavy_count = sum(
            (
                add(prompt_chars >= 50_000, "large-context"),
                add(prompt_chars >= 24_000, "medium-large-context"),
                add(message_count >= 16, "long-conversation"),
                add(tool_count >= 4, "many-tools"),
                add(output_tokens >= 8_192, "large-output"),
                add(high_effort, "high-reasoning-effort"),
                add(critical_keyword, "security-sensitive"),
                add(heavy_keyword and prompt_chars >= 4_000, "complex-task"),
            )
        )
        if heavy_count >= 2 or prompt_chars >= 50_000 or high_effort:
            level = "heavy"
        elif (
            (
                prompt_chars <= 2_000
                and message_count <= 3
                and tool_count == 0
                and output_tokens <= 1_500
                and light_effort
                and not critical_keyword
                and not heavy_keyword
            )
            or (
                light_keyword
                and prompt_chars <= 4_000
                and tool_count == 0
                and light_effort
                and not critical_keyword
            )
        ):
            level = "light"
            if not reasons:
                reasons.append("small-simple-request")
        else:
            level = "standard"
            if not reasons:
                reasons.append("default")

    return TaskClassification(
        intent=classify_prompt_intent(prompt, system_prompt),
        level=level,
        reasons=tuple(dict.fromkeys(reasons)),
        prompt_chars=prompt_chars,
        message_count=max(0, int(message_count)),
        tool_count=max(0, int(tool_count)),
        output_tokens=max(0, int(output_tokens)),
    )
