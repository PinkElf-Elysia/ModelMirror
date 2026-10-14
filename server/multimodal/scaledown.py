"""Text-only ScaleDown contracts; no arbitrary provider options or URLs."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScaleDownRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model: Literal["scaledown/summarize", "scaledown/extract"]
    text: str = Field(min_length=1, max_length=100_000)
    instructions: str = Field(default="", max_length=4_000)
    entities: dict[str, str] | None = None
    max_tokens: int = Field(default=1_000, ge=1, le=4_000)

    @model_validator(mode="after")
    def validate_contract(self):
        if not self.text.strip():
            raise ValueError("请输入待处理的文字。")
        if self.model == "scaledown/summarize":
            if self.max_tokens > 1000:
                raise ValueError("摘要输出上限为 1000 Token。")
            if self.entities is not None:
                raise ValueError("摘要不接受提取字段。")
        else:
            if not self.entities or len(self.entities) > 50:
                raise ValueError("提取需要 1 至 50 个字段。")
            for name, description in self.entities.items():
                if not name.strip() or len(name) > 80:
                    raise ValueError("字段名称须为 1 至 80 字符。")
                if not description.strip() or len(description) > 1_000:
                    raise ValueError("字段描述须为 1 至 1000 字符。")
        return self

    def upstream_payload(self) -> dict:
        messages = []
        if self.instructions.strip():
            messages.append({"role": "system", "content": self.instructions.strip()})
        messages.append({"role": "user", "content": self.text})
        body = {"model": self.model, "messages": messages,
                "stream": False, "max_tokens": self.max_tokens}
        if self.entities is not None:
            body["provider"] = {"options": {"scaledown": {"entities": self.entities}}}
        return body


def scaledown_result(body: object, expected_model: str) -> dict:
    try:
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        if body["choices"][0].get("finish_reason") == "length":
            raise ValueError
        if expected_model == "scaledown/summarize":
            parsed = json.loads(content)
            if not isinstance(parsed, dict) or parsed.get("finish_reason") == "length":
                raise ValueError
            content = parsed.get("summary")
            if not isinstance(content, str) or not content.strip():
                raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("上游未返回完整的文字结果，请检查输入和输出上限。") from None
    # Do not relay arbitrary upstream response fields or errors.
    return {"model": expected_model, "content": content}
