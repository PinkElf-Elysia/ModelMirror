from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .document_processor import ProcessedDocument


@dataclass(slots=True)
class GeneratedIndexItem:
    item_id: str
    index_text: str
    context_text: str
    source_block_ids: list[str]
    item_type: str

    def payload(self, *, max_text: int | None = None) -> dict[str, Any]:
        index_text = self.index_text
        context_text = self.context_text
        truncated = False
        if max_text is not None:
            if len(index_text) > max_text:
                index_text = index_text[:max_text] + "..."
                truncated = True
            if len(context_text) > max_text:
                context_text = context_text[:max_text] + "..."
                truncated = True
        return {
            "item_id": self.item_id,
            "index_text": index_text,
            "context_text": context_text,
            "source_block_ids": list(self.source_block_ids),
            "item_type": self.item_type,
            "truncated": truncated,
        }


class ProcessorGenerationError(RuntimeError):
    """Raised when all configured generation targets or attempts fail."""


class ProcessorGenerationService:
    """Generate strict QA or summary index items through existing LLM gateways."""

    def capabilities(self) -> dict[str, Any]:
        targets = self._targets()
        model = self.default_model()
        return {
            "llm_configured": bool(targets and model),
            "model": model if targets else "",
            "targets": [name for name, _, _ in targets],
        }

    def default_model(self) -> str:
        return (
            os.getenv("RAG_PROCESSOR_MODEL", "").strip()
            or os.getenv("OPENROUTER_TEXT_FALLBACK_MODEL", "").strip()
            or "deepseek/deepseek-chat"
        )

    async def generate(
        self,
        document: ProcessedDocument,
        *,
        mode: str,
        model_id: str,
        max_items: int,
    ) -> list[GeneratedIndexItem]:
        if mode not in {"qa", "summary"}:
            return []
        source_blocks = [block for block in document.blocks if block.text.strip()]
        if not source_blocks:
            raise ProcessorGenerationError("Document contains no blocks for generation.")
        compact = [
            {
                "block_id": block.block_id,
                "kind": block.kind,
                "heading_path": block.heading_path,
                "page_number": block.page_number,
                "text": block.text[:5000],
            }
            for block in source_blocks[:100]
        ]
        if mode == "qa":
            batches = self._block_batches(compact)
        else:
            summary_blocks: list[dict[str, Any]] = []
            summary_chars = 0
            for block in compact:
                size = len(str(block.get("text") or ""))
                if summary_blocks and summary_chars + size > 60_000:
                    break
                summary_blocks.append(block)
                summary_chars += size
            batches = [summary_blocks]
        generated: list[GeneratedIndexItem] = []
        for batch in batches:
            remaining = max_items - len(generated)
            if remaining <= 0:
                break
            data = await self._generate_batch(
                mode=mode,
                model_id=model_id or self.default_model(),
                title=document.title,
                blocks=batch,
                max_items=remaining,
            )
            generated.extend(
                self._parse_items(
                    data,
                    document=document,
                    mode=mode,
                    max_items=remaining,
                )
            )
        if not generated:
            raise ProcessorGenerationError(f"Processor model returned no valid {mode} items.")
        for index, item in enumerate(generated[:max_items]):
            item.item_id = f"{mode}_{index}"
        return generated[:max_items]

    async def _generate_batch(
        self,
        *,
        mode: str,
        model_id: str,
        title: str,
        blocks: list[dict[str, Any]],
        max_items: int,
    ) -> dict[str, Any]:
        payload = self._payload(
            mode=mode,
            model_id=model_id,
            title=title,
            blocks=blocks,
            max_items=max_items,
        )
        errors: list[str] = []
        for attempt in range(2):
            for target_name, target_url, target_key in self._targets():
                try:
                    return await self._post(target_url, target_key, payload)
                except Exception as exc:
                    errors.append(
                        f"attempt={attempt + 1} target={target_name}: {str(exc)[:140]}"
                    )
        raise ProcessorGenerationError(
            "; ".join(errors[-4:]) or "No processor LLM target is configured."
        )

    def _block_batches(self, blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_chars = 0
        for block in blocks:
            size = len(str(block.get("text") or ""))
            if current and (len(current) >= 6 or current_chars + size > 20_000):
                batches.append(current)
                current = []
                current_chars = 0
            current.append(block)
            current_chars += size
        if current:
            batches.append(current)
        return batches

    async def _post(self, url: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        content = message.get("content") if isinstance(message, dict) else None
        parsed = _parse_json_object(str(content or ""))
        if not isinstance(parsed, dict):
            raise ValueError("Processor model response must be a JSON object.")
        return parsed

    def _payload(
        self,
        *,
        mode: str,
        model_id: str,
        title: str,
        blocks: list[dict[str, Any]],
        max_items: int,
    ) -> dict[str, Any]:
        if mode == "qa":
            contract = (
                'Return JSON only: {"items":[{"question":"...","answer":"...",'
                '"block_ids":["block_id"]}]}. Produce grounded questions and concise answers.'
            )
        else:
            contract = (
                'Return JSON only: {"document_summary":"...","sections":['
                '{"summary":"...","block_ids":["block_id"]}]}. Keep every summary grounded.'
            )
        return {
            "model": model_id,
            "messages": [
                {"role": "system", "content": contract},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"title": title, "max_items": max_items, "blocks": blocks},
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 4000,
            "stream": False,
        }

    def _parse_items(
        self,
        data: dict[str, Any],
        *,
        document: ProcessedDocument,
        mode: str,
        max_items: int,
    ) -> list[GeneratedIndexItem]:
        blocks = {block.block_id: block for block in document.blocks}
        raw_items: list[dict[str, Any]] = []
        if mode == "qa":
            value = data.get("items")
            if isinstance(value, list):
                raw_items = [item for item in value if isinstance(item, dict)]
        else:
            document_summary = data.get("document_summary")
            if isinstance(document_summary, str) and document_summary.strip():
                raw_items.append(
                    {
                        "summary": document_summary,
                        "block_ids": list(blocks)[: min(20, len(blocks))],
                    }
                )
            sections = data.get("sections")
            if isinstance(sections, list):
                raw_items.extend(item for item in sections if isinstance(item, dict))

        items: list[GeneratedIndexItem] = []
        for index, raw in enumerate(raw_items[:max_items]):
            raw_ids = raw.get("block_ids")
            block_ids = [str(item) for item in raw_ids if str(item) in blocks] if isinstance(raw_ids, list) else []
            if not block_ids:
                continue
            source_text = "\n\n".join(blocks[item].text for item in block_ids)[:12000]
            if mode == "qa":
                question = str(raw.get("question") or "").strip()
                answer = str(raw.get("answer") or "").strip()
                if not question or not answer:
                    continue
                index_text = question
                context_text = f"Question: {question}\nAnswer: {answer}\n\nSource:\n{source_text}"
            else:
                summary = str(raw.get("summary") or "").strip()
                if not summary:
                    continue
                index_text = summary
                context_text = f"Summary: {summary}\n\nSource:\n{source_text}"
            items.append(
                GeneratedIndexItem(
                    item_id=f"{mode}_{index}",
                    index_text=index_text,
                    context_text=context_text,
                    source_block_ids=block_ids,
                    item_type=mode,
                )
            )
        if not items:
            raise ValueError(f"Processor model returned no valid {mode} items.")
        return items

    def _targets(self) -> list[tuple[str, str, str]]:
        targets: list[tuple[str, str, str]] = []
        gateway = os.getenv("LLM_GATEWAY_URL", "").strip().rstrip("/")
        gateway_key = os.getenv("LLM_GATEWAY_KEY", "").strip()
        if gateway and gateway_key:
            url = gateway if gateway.endswith("/chat/completions") else f"{gateway}/chat/completions"
            targets.append(("llm_gateway", url, gateway_key))
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if openrouter_key:
            targets.append(("openrouter", "https://openrouter.ai/api/v1/chat/completions", openrouter_key))
        return targets


def _parse_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Processor response must be a JSON object.")
    return value
