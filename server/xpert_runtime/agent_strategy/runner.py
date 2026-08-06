from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..toolset import RuntimeTool, RuntimeToolError, RuntimeToolResult
from .models import (
    AgentModelClient,
    AgentModelError,
    AgentStrategyError,
    AgentStrategyEvent,
    AgentStrategyName,
    AgentStrategyResult,
    AgentToolCall,
    AgentUsage,
)
from .react import build_react_prompt, parse_react_decision


ToolExecutor = Callable[[str, dict[str, Any], str, int], Awaitable[RuntimeToolResult]]

MAX_TOOL_CALLS_PER_ROUND = 8
MAX_OBSERVATION_CHARS = 16_000
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|authorization|cookie|password|secret|token)",
    re.IGNORECASE,
)
FUNCTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(slots=True)
class ToolBinding:
    alias: str
    tool: RuntimeTool
    schema: dict[str, Any]
    validator: Draft202012Validator


@dataclass(slots=True)
class ToolOutcome:
    call_id: str
    alias: str
    tool_name: str
    observation: str
    event: AgentStrategyEvent
    attempted: bool = False
    executed: bool = False


class AgentStrategyRunner:
    def __init__(
        self,
        *,
        model_client: AgentModelClient,
        tool_executor: ToolExecutor,
        tools: list[RuntimeTool],
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        strategy: AgentStrategyName = "auto",
        max_iterations: int = 5,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        parallel_tool_calls: bool = False,
        history_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        if strategy not in {"auto", "function_calling", "react"}:
            raise ValueError(f"Unsupported agent strategy: {strategy}")
        self.model_client = model_client
        self.tool_executor = tool_executor
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.requested_strategy = strategy
        self.max_iterations = min(max(int(max_iterations), 1), 20)
        self.temperature = min(max(float(temperature), 0.0), 2.0)
        self.max_tokens = max(int(max_tokens), 1)
        self.parallel_tool_calls = bool(parallel_tool_calls)
        self.history_messages = [
            deepcopy(message)
            for message in list(history_messages or [])
            if str(message.get("role") or "") in {"user", "assistant"}
            and str(message.get("content") or "").strip()
        ]
        self.events: list[AgentStrategyEvent] = []
        self.usage = AgentUsage()
        self.tool_calls_attempted = 0
        self.tool_calls_executed = 0
        self.bindings = build_tool_bindings(tools)
        self.binding_by_alias = {binding.alias: binding for binding in self.bindings}
        self.binding_by_name = {binding.tool.name: binding for binding in self.bindings}

    async def run(self) -> AgentStrategyResult:
        try:
            if self.requested_strategy == "react":
                return await self._run_react()
            if self.requested_strategy == "function_calling":
                return await self._run_function_calling()
            try:
                return await self._run_function_calling()
            except AgentModelError as exc:
                if self.tool_calls_attempted or not exc.is_function_calling_unsupported():
                    raise
                self.events.append(
                    AgentStrategyEvent(
                        event_type="strategy_fallback",
                        strategy="react",
                        status="warning",
                        message="模型不支持原生 Function Calling，已安全回退 ReAct。",
                        metadata={"reason": exc.message, "status_code": exc.status_code},
                    )
                )
                return await self._run_react()
        except AgentStrategyError:
            raise
        except AgentModelError as exc:
            raise self._error(exc.message, code="model_error") from exc
        except Exception as exc:
            if hasattr(exc, "continuation") and hasattr(exc, "approval_id"):
                raise
            raise self._error(str(exc) or exc.__class__.__name__) from exc

    async def _run_function_calling(self) -> AgentStrategyResult:
        self.events.append(
            AgentStrategyEvent(
                event_type="strategy_selected",
                strategy="function_calling",
                message="使用原生 Function Calling 策略。",
            )
        )
        tool_definitions = [binding_to_openai_tool(binding) for binding in self.bindings]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            *deepcopy(self.history_messages),
            {"role": "user", "content": self.user_prompt},
        ]

        for iteration in range(1, self.max_iterations + 1):
            turn = await self.model_client.complete(
                model_id=self.model_id,
                messages=deepcopy(messages),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=deepcopy(tool_definitions),
                tool_choice="auto",
                parallel_tool_calls=self.parallel_tool_calls,
            )
            self.usage.add(turn.usage)
            self.events.append(
                AgentStrategyEvent(
                    event_type="model_round",
                    strategy="function_calling",
                    iteration=iteration,
                    message=f"Function Calling 第 {iteration} 轮完成。",
                    metadata={
                        "finish_reason": turn.finish_reason,
                        "usage": turn.usage.to_dict(),
                    },
                )
            )
            if not turn.tool_calls:
                answer = turn.content.strip()
                if not answer:
                    raise self._error("模型没有返回答案或工具调用。", code="empty_model_response")
                return self._result(answer, "function_calling")

            messages.append(_assistant_tool_call_message(turn.content, turn.tool_calls))
            outcomes = await self._execute_function_call_batch(turn.tool_calls, iteration)
            for outcome in outcomes:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": outcome.call_id,
                        "name": outcome.alias,
                        "content": outcome.observation,
                    }
                )
            terminal_outcome = next(
                (
                    outcome
                    for outcome in outcomes
                    if outcome.executed
                    and (self.binding_by_alias.get(outcome.alias) is not None)
                    and self.binding_by_alias[outcome.alias].tool.terminal
                ),
                None,
            )
            if terminal_outcome is not None:
                return self._result(
                    terminal_outcome.observation,
                    "function_calling",
                )

        final_turn = await self.model_client.complete(
            model_id=self.model_id,
            messages=deepcopy(messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            tools=deepcopy(tool_definitions),
            tool_choice="none",
            parallel_tool_calls=False,
        )
        self.usage.add(final_turn.usage)
        self.events.append(
            AgentStrategyEvent(
                event_type="model_round",
                strategy="function_calling",
                iteration=self.max_iterations,
                message="Function Calling 已执行禁止工具的最终总结。",
                metadata={
                    "finish_reason": final_turn.finish_reason,
                    "final_summary": True,
                    "usage": final_turn.usage.to_dict(),
                },
            )
        )
        if final_turn.tool_calls or not final_turn.content.strip():
            self.events.append(
                AgentStrategyEvent(
                    event_type="iteration_limit",
                    strategy="function_calling",
                    iteration=self.max_iterations,
                    status="warning",
                    message=f"达到最大工具循环次数 {self.max_iterations}。",
                )
            )
            raise self._error(
                f"Agent 达到最大工具循环次数 {self.max_iterations}，且未生成最终答案。",
                code="iteration_limit",
            )
        return self._result(final_turn.content.strip(), "function_calling")

    async def _run_react(self) -> AgentStrategyResult:
        self.events.append(
            AgentStrategyEvent(
                event_type="strategy_selected",
                strategy="react",
                message="使用 ReAct 策略。",
            )
        )
        tools_text = "\n".join(
            f"- {binding.tool.name}: {binding.tool.description or '无描述'} "
            f"schema={json.dumps(binding.schema, ensure_ascii=False)}"
            for binding in self.bindings
        )
        react_system_prompt = build_react_prompt(
            system_prompt=self.system_prompt,
            tools_text=tools_text,
            tool_names=[binding.tool.name for binding in self.bindings],
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": react_system_prompt},
            *deepcopy(self.history_messages),
            {"role": "user", "content": self.user_prompt},
        ]

        for iteration in range(1, self.max_iterations + 1):
            turn = await self.model_client.complete(
                model_id=self.model_id,
                messages=deepcopy(messages),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            self.usage.add(turn.usage)
            decision = parse_react_decision(turn.content)
            self.events.append(
                AgentStrategyEvent(
                    event_type="model_round",
                    strategy="react",
                    iteration=iteration,
                    status="warning" if decision.kind == "invalid" else "info",
                    message=f"ReAct 第 {iteration} 轮：{decision.kind}。",
                    metadata={"usage": turn.usage.to_dict()},
                )
            )
            if decision.kind == "answer":
                return self._result(decision.answer.strip(), "react")
            if decision.kind == "invalid":
                messages.append({"role": "assistant", "content": turn.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Observation: 格式错误：{decision.error}\n"
                            "请只返回一个 Action JSON，或返回 FinalAnswer。"
                        ),
                    }
                )
                continue

            outcome = await self._execute_tool_call(
                AgentToolCall(
                    call_id=f"react_{iteration}",
                    name=decision.action,
                    raw_arguments=json.dumps(decision.action_input or {}, ensure_ascii=False),
                ),
                iteration,
                react_names=True,
            )
            self.events.append(outcome.event)
            self.tool_calls_attempted += int(outcome.attempted)
            self.tool_calls_executed += int(outcome.executed)
            binding = self.binding_by_name.get(outcome.tool_name)
            if outcome.executed and binding is not None and binding.tool.terminal:
                return self._result(outcome.observation, "react")
            messages.append({"role": "assistant", "content": turn.content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Observation: {outcome.observation}\n"
                        "继续时返回一个 Action JSON；完成时返回 FinalAnswer。"
                    ),
                }
            )

        messages.append(
            {
                "role": "user",
                "content": (
                    f"工具循环已达到上限 {self.max_iterations}。"
                    "不得再调用工具，只返回 FinalAnswer。"
                ),
            }
        )
        final_turn = await self.model_client.complete(
            model_id=self.model_id,
            messages=deepcopy(messages),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        self.usage.add(final_turn.usage)
        self.events.append(
            AgentStrategyEvent(
                event_type="model_round",
                strategy="react",
                iteration=self.max_iterations,
                message="ReAct 已执行禁止工具的最终总结。",
                metadata={
                    "final_summary": True,
                    "usage": final_turn.usage.to_dict(),
                },
            )
        )
        decision = parse_react_decision(final_turn.content)
        if decision.kind == "answer" and decision.answer.strip():
            return self._result(decision.answer.strip(), "react")
        self.events.append(
            AgentStrategyEvent(
                event_type="iteration_limit",
                strategy="react",
                iteration=self.max_iterations,
                status="warning",
                message=f"达到最大工具循环次数 {self.max_iterations}。",
            )
        )
        raise self._error(
            f"Agent 达到最大工具循环次数 {self.max_iterations}，且未生成最终答案。",
            code="iteration_limit",
        )

    async def _execute_function_call_batch(
        self,
        calls: list[AgentToolCall],
        iteration: int,
    ) -> list[ToolOutcome]:
        limited_calls = calls[:MAX_TOOL_CALLS_PER_ROUND]
        overflow = calls[MAX_TOOL_CALLS_PER_ROUND:]
        if self.parallel_tool_calls and len(limited_calls) > 1:
            outcomes = list(
                await asyncio.gather(
                    *(self._execute_tool_call(call, iteration) for call in limited_calls)
                )
            )
        else:
            outcomes = []
            for call in limited_calls:
                outcomes.append(await self._execute_tool_call(call, iteration))

        for call in overflow:
            binding = self.binding_by_alias.get(call.name)
            tool_name = binding.tool.name if binding else call.name
            outcomes.append(
                self._rejected_outcome(
                    call,
                    iteration,
                    tool_name=tool_name,
                    message=f"单轮工具调用不能超过 {MAX_TOOL_CALLS_PER_ROUND} 个。",
                )
            )
        for outcome in outcomes:
            self.events.append(outcome.event)
            self.tool_calls_attempted += int(outcome.attempted)
            self.tool_calls_executed += int(outcome.executed)
        return outcomes

    async def _execute_tool_call(
        self,
        call: AgentToolCall,
        iteration: int,
        *,
        react_names: bool = False,
    ) -> ToolOutcome:
        binding = (
            self.binding_by_name.get(call.name)
            if react_names
            else self.binding_by_alias.get(call.name)
        )
        if binding is None:
            return self._rejected_outcome(
                call,
                iteration,
                tool_name=call.name,
                message=f"工具不可用：{call.name}",
            )
        try:
            arguments = json.loads(call.raw_arguments or "{}")
        except ValueError:
            return self._rejected_outcome(
                call,
                iteration,
                tool_name=binding.tool.name,
                message="工具参数不是有效 JSON。",
            )
        if not isinstance(arguments, dict):
            return self._rejected_outcome(
                call,
                iteration,
                tool_name=binding.tool.name,
                message="工具参数必须是 JSON 对象。",
            )
        validation_errors = sorted(
            binding.validator.iter_errors(arguments),
            key=lambda error: list(error.absolute_path),
        )
        if validation_errors:
            details = "; ".join(_validation_error_text(error) for error in validation_errors[:3])
            return self._rejected_outcome(
                call,
                iteration,
                tool_name=binding.tool.name,
                message=f"工具参数校验失败：{details}",
                arguments=arguments,
            )

        started_at = time.perf_counter()
        try:
            result = await self.tool_executor(
                binding.tool.name,
                arguments,
                call.call_id,
                iteration,
            )
        except RuntimeToolError as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            if exc.code in {"tool_denied", "capability_not_found"}:
                self.events.append(
                    AgentStrategyEvent(
                        event_type="tool_call",
                        strategy=self._active_strategy(react_names),
                        iteration=iteration,
                        status="failed",
                        message=f"工具 {binding.tool.name} 被运行时拒绝：{exc.message}",
                        tool_name=binding.tool.name,
                        tool_call_id=call.call_id,
                        arguments_summary=summarize_arguments(arguments),
                        duration_ms=duration_ms,
                        metadata={"error_code": exc.code},
                    )
                )
                raise self._error(exc.message, code=exc.code, attempted_increment=1) from exc
            observation = truncate_observation(f"工具执行失败：{exc.message}")
            return ToolOutcome(
                call_id=call.call_id,
                alias=binding.alias,
                tool_name=binding.tool.name,
                observation=observation,
                attempted=True,
                executed=False,
                event=AgentStrategyEvent(
                    event_type="tool_call",
                    strategy=self._active_strategy(react_names),
                    iteration=iteration,
                    status="failed",
                    message=f"工具 {binding.tool.name} 执行失败，可由模型恢复。",
                    tool_name=binding.tool.name,
                    tool_call_id=call.call_id,
                    arguments_summary=summarize_arguments(arguments),
                    output_preview=observation[:300],
                    duration_ms=duration_ms,
                    metadata={"error_code": exc.code},
                ),
            )
        except Exception as exc:
            if hasattr(exc, "continuation") and hasattr(exc, "approval_id"):
                raise
            raise self._error(
                str(exc) or exc.__class__.__name__,
                code="tool_system_error",
                attempted_increment=1,
            ) from exc

        duration_ms = (time.perf_counter() - started_at) * 1000
        observation = format_tool_observation(result)
        status = "failed" if result.is_error else "completed"
        return ToolOutcome(
            call_id=call.call_id,
            alias=binding.alias,
            tool_name=binding.tool.name,
            observation=observation,
            attempted=True,
            executed=not result.is_error,
            event=AgentStrategyEvent(
                event_type="tool_call",
                strategy=self._active_strategy(react_names),
                iteration=iteration,
                status=status,
                message=f"工具 {binding.tool.name} {status}。",
                tool_name=binding.tool.name,
                tool_call_id=call.call_id,
                arguments_summary=summarize_arguments(arguments),
                output_preview=observation[:300],
                duration_ms=duration_ms,
                metadata={
                    "content_types": list(result.metadata.get("content_types", [])),
                    "is_error": result.is_error,
                },
            ),
        )

    def _rejected_outcome(
        self,
        call: AgentToolCall,
        iteration: int,
        *,
        tool_name: str,
        message: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolOutcome:
        observation = truncate_observation(message)
        return ToolOutcome(
            call_id=call.call_id,
            alias=call.name,
            tool_name=tool_name,
            observation=observation,
            attempted=False,
            executed=False,
            event=AgentStrategyEvent(
                event_type="tool_call",
                strategy="react" if call.call_id.startswith("react_") else "function_calling",
                iteration=iteration,
                status="rejected",
                message=message,
                tool_name=tool_name,
                tool_call_id=call.call_id,
                arguments_summary=summarize_arguments(arguments or {}),
                output_preview=observation[:300],
            ),
        )

    def _active_strategy(self, react_names: bool) -> str:
        return "react" if react_names else "function_calling"

    def _result(
        self,
        answer: str,
        strategy: str,
    ) -> AgentStrategyResult:
        self.events.append(
            AgentStrategyEvent(
                event_type="final_answer",
                strategy=strategy,
                status="completed",
                message=f"Agent 已生成最终答案（{len(answer)} 字符）。",
                metadata={
                    "answer_length": len(answer),
                    "usage": self.usage.to_dict(),
                },
            )
        )
        return AgentStrategyResult(
            answer=answer,
            strategy=strategy,  # type: ignore[arg-type]
            events=list(self.events),
            usage=self.usage,
            tool_calls_attempted=self.tool_calls_attempted,
            tool_calls_executed=self.tool_calls_executed,
        )

    def _error(
        self,
        message: str,
        *,
        code: str = "agent_strategy_error",
        attempted_increment: int = 0,
    ) -> AgentStrategyError:
        attempted = self.tool_calls_attempted + attempted_increment
        return AgentStrategyError(
            message,
            code=code,
            events=list(self.events),
            usage=self.usage,
            tool_calls_attempted=attempted,
            tool_calls_executed=self.tool_calls_executed,
        )


def build_tool_bindings(tools: list[RuntimeTool]) -> list[ToolBinding]:
    bindings: list[ToolBinding] = []
    used_aliases: set[str] = set()
    for index, tool in enumerate(tools, start=1):
        if not tool.name:
            continue
        schema = normalize_tool_schema(tool.input_schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise AgentStrategyError(
                f"工具 {tool.name} 的 JSON Schema 无效：{exc.message}",
                code="invalid_tool_schema",
            ) from exc
        alias = tool.name if FUNCTION_NAME_PATTERN.fullmatch(tool.name) else _tool_alias(tool.name, index)
        if alias in used_aliases:
            alias = _tool_alias(tool.name, index, force_hash=True)
        used_aliases.add(alias)
        bindings.append(
            ToolBinding(
                alias=alias,
                tool=tool,
                schema=schema,
                validator=Draft202012Validator(schema),
            )
        )
    return bindings


def normalize_tool_schema(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {"type": "object", "properties": {}}
    schema = deepcopy(value)
    if "type" not in schema and ("properties" in schema or "required" in schema):
        schema["type"] = "object"
    if schema.get("type") != "object":
        raise AgentStrategyError(
            "工具输入 Schema 的根类型必须是 object。",
            code="invalid_tool_schema",
        )
    schema.setdefault("properties", {})
    return schema


def binding_to_openai_tool(binding: ToolBinding) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": binding.alias,
            "description": binding.tool.description or f"调用 {binding.tool.name}",
            "parameters": deepcopy(binding.schema),
        },
    }


def format_tool_observation(result: RuntimeToolResult) -> str:
    parts: list[str] = []
    output = str(result.output or "").strip()
    if output:
        parts.append(output)
    content_types_raw = result.metadata.get("content_types", [])
    content_types = [str(item) for item in content_types_raw] if isinstance(content_types_raw, list) else []
    non_text_types = sorted({item for item in content_types if item != "text"})
    if non_text_types:
        parts.append("工具还返回了非文本内容：" + ", ".join(non_text_types))
    if not parts:
        parts.append("工具执行完成，但没有返回文本内容。")
    if result.is_error:
        parts.insert(0, "工具报告执行错误。")
    return truncate_observation("\n".join(parts))


def truncate_observation(value: str, limit: int = MAX_OBSERVATION_CHARS) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return value[:limit] + f"\n[工具结果已截断，省略 {omitted} 个字符]"


def summarize_arguments(arguments: dict[str, Any]) -> str:
    redacted = _redact(arguments)
    serialized = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
    return serialized if len(serialized) <= 500 else serialized[:500] + "…"


def _redact(value: Any, key: str = "") -> Any:
    if key and SENSITIVE_KEY_PATTERN.search(key):
        return "***"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _assistant_tool_call_message(content: str, calls: list[AgentToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments,
                },
            }
            for call in calls
        ],
    }


def _tool_alias(name: str, index: int, *, force_hash: bool = False) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_-") or "tool"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    prefix = f"tool_{index}_"
    suffix = f"_{digest}" if force_hash or safe != name else ""
    available = 64 - len(prefix) - len(suffix)
    return f"{prefix}{safe[:available]}{suffix}"


def _validation_error_text(error: Any) -> str:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return f"{path}: {error.message}"
