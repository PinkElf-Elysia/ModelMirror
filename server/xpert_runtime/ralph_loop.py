from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ModelText = Callable[[str, list[dict[str, Any]], int], Awaitable[str]]
ContinueAgent = Callable[[str, int], Awaitable[str]]
Checkpoint = Callable[[str, str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class RalphLoopResult:
    output: str
    iterations: int
    verified: bool
    reason: str
    budget_limited: bool = False


async def run_ralph_loop(
    initial_output: str,
    *,
    objective: str,
    model_id: str,
    verifier_model_id: str,
    max_iterations: int,
    max_output_chars: int,
    model_text: ModelText,
    continue_agent: ContinueAgent,
    checkpoint: Checkpoint | None = None,
) -> RalphLoopResult:
    """Bounded act/verify loop with strict evidence-based JSON verification."""

    output = str(initial_output or "")
    seen: set[str] = set()
    for iteration in range(1, max(1, min(int(max_iterations), 20)) + 1):
        digest = hashlib.sha256(_normalize(output).encode("utf-8")).hexdigest()
        if digest in seen:
            reason = "No progress: the candidate output repeated."
            if checkpoint:
                await checkpoint("ralph.no_progress", reason, {"iteration": iteration})
            return RalphLoopResult(output, iteration, False, reason)
        seen.add(digest)
        verification = await _verify(
            output,
            objective=objective,
            model_id=verifier_model_id or model_id,
            model_text=model_text,
        )
        if checkpoint:
            await checkpoint(
                "ralph.verified" if verification["complete"] else "ralph.continue",
                verification["reason"],
                {"iteration": iteration, "complete": verification["complete"]},
            )
        if verification["complete"]:
            return RalphLoopResult(output, iteration, True, verification["reason"])
        if len(output) >= max_output_chars:
            return RalphLoopResult(
                output,
                iteration,
                False,
                "Ralph output budget reached.",
                budget_limited=True,
            )
        instruction = (
            "Continue the task using the verifier feedback. Preserve correct work, fix the gaps, "
            "and return a complete replacement final answer.\n\n"
            f"Objective:\n{objective[:12000]}\n\n"
            f"Current candidate:\n{output[:20000]}\n\n"
            f"Verifier feedback:\n{verification['feedback'][:4000]}"
        )
        output = await continue_agent(instruction, iteration)
    return RalphLoopResult(
        output,
        max(1, min(int(max_iterations), 20)),
        False,
        "Ralph iteration budget reached.",
        budget_limited=True,
    )


async def _verify(
    candidate: str,
    *,
    objective: str,
    model_id: str,
    model_text: ModelText,
) -> dict[str, Any]:
    prompt = (
        "Judge whether the candidate fully satisfies the objective using only evidence in the "
        "candidate. Return strict JSON only: "
        '{"complete":true|false,"reason":"short reason","feedback":"specific next action"}. '
        "Do not mark complete when required outputs are missing or merely promised.\n\n"
        f"Objective:\n{objective[:12000]}\n\nCandidate:\n{candidate[:24000]}"
    )
    raw = await model_text(model_id, [{"role": "user", "content": prompt}], 800)
    try:
        parsed = json.loads(_json_text(raw))
        if not isinstance(parsed, dict) or not isinstance(parsed.get("complete"), bool):
            raise ValueError("missing boolean complete")
        return {
            "complete": parsed["complete"],
            "reason": str(parsed.get("reason") or "Verification completed.")[:1000],
            "feedback": str(parsed.get("feedback") or parsed.get("reason") or "Improve the result.")[:4000],
        }
    except Exception as exc:
        return {
            "complete": False,
            "reason": f"Verifier returned invalid JSON: {str(exc)[:160]}",
            "feedback": "Review the objective and return a more explicit, complete result.",
        }


def _json_text(text: str) -> str:
    stripped = str(text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.I | re.S)
    return match.group(1).strip() if match else stripped


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip()).lower()
