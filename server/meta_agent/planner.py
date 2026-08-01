from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from typing import Any

from .prompts import build_generation_prompt
from .schemas import (
    MetaAgentGeneratedAgent,
    MetaAgentPlan,
    MetaAgentSubTask,
)


def build_meta_agent_prompt(goal: str, max_tasks: int) -> str:
    return build_generation_prompt(goal=goal, max_tasks=max_tasks)


def parse_meta_agent_plan(raw_text: str, max_tasks: int = 5) -> MetaAgentPlan:
    payload = json.loads(extract_json_object_text(raw_text))
    if not isinstance(payload, dict):
        raise ValueError("Meta-agent output must be a JSON object.")

    if "sub_tasks" not in payload:
        if isinstance(payload.get("plan"), dict):
            payload = {**payload["plan"], "thought": payload.get("thought", "")}
        elif isinstance(payload.get("workflow"), dict):
            payload = {**payload["workflow"], "thought": payload.get("thought", "")}
        elif isinstance(payload.get("tasks"), list):
            payload["sub_tasks"] = payload["tasks"]
        elif isinstance(payload.get("subtasks"), list):
            payload["sub_tasks"] = payload["subtasks"]

    tasks = payload.get("sub_tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Meta-agent output must include a non-empty sub_tasks list.")

    payload["sub_tasks"] = [_normalize_task_payload(item) for item in tasks[:max_tasks]]
    plan = MetaAgentPlan.model_validate(payload)
    if not plan.sub_tasks:
        raise ValueError("Meta-agent plan is empty after normalization.")
    return plan


def extract_json_object_text(raw_text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = raw_text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in meta-agent output.")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw_text)):
        char = raw_text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw_text[start : index + 1].strip()

    raise ValueError("Unclosed JSON object in meta-agent output.")


def infer_task_edges(tasks: list[MetaAgentSubTask]) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for source in tasks:
        source_outputs = {param.name for param in source.outputs}
        if not source_outputs:
            continue
        for target in tasks:
            if source.name == target.name:
                continue
            target_inputs = {param.name for param in target.inputs}
            if source_outputs & target_inputs:
                edges.append((source.name, target.name))
    return edges


def build_workflow_from_plan(
    *,
    goal: str,
    plan: MetaAgentPlan,
    model_id: str,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    tasks = plan.sub_tasks
    task_ids = _unique_task_ids(tasks)
    task_edges = infer_task_edges(tasks)
    incoming: dict[str, set[str]] = {task.name: set() for task in tasks}
    outgoing: dict[str, set[str]] = {task.name: set() for task in tasks}
    for source, target in task_edges:
        outgoing[source].add(target)
        incoming[target].add(source)

    known_variables = {"goal": "goal", "user_input": "goal"}
    task_output_variables: dict[str, str] = {}

    nodes: list[dict[str, Any]] = [
        {
            "id": "input_goal",
            "type": "workflowNode",
            "position": {"x": 0, "y": 120},
            "data": {
                "kind": "input",
                "title": "Goal input",
                "description": "The natural-language goal provided to the meta-agent.",
                "variableName": "goal",
            },
        }
    ]

    ordered_tasks = _topological_tasks(tasks, task_edges)
    for index, task in enumerate(ordered_tasks, start=1):
        node_id = task_ids[task.name]
        input_aliases: dict[str, str] = {}
        for param in task.inputs:
            safe_name = known_variables.get(param.name)
            if not safe_name:
                safe_name = "goal"
                warnings.append(
                    f"Task {task.name} referenced unbound input {param.name}; "
                    "falling back to goal."
                )
            input_aliases[param.name] = safe_name
        if not task.inputs and index == 1:
            input_aliases["goal"] = "goal"

        if not task.outputs:
            output_name = f"{node_id}_output"
            warnings.append(
                f"Task {task.name} did not declare outputs; added {output_name}."
            )
        else:
            output_name = _safe_identifier(task.outputs[0].name, f"{node_id}_output")

        task_output_variables[task.name] = output_name
        for output in task.outputs:
            known_variables[output.name] = _safe_identifier(output.name, output_name)

        agent = _primary_agent(task)
        instruction = _agent_prompt(task=task, agent=agent)
        instruction = _convert_placeholders(instruction, input_aliases)
        instruction = _repair_unbound_template_variables(
            instruction=instruction,
            allowed=set(input_aliases.values()),
            fallback="goal",
            warnings=warnings,
            task_name=task.name,
        )
        if output_name not in instruction:
            instruction = (
                f"{instruction.rstrip()}\n\n"
                "### Output Format\n"
                f"## {output_name}\n"
                f"Provide the final output for {task.description}."
            )

        tool_names = _clean_tool_names(agent.tool_names if agent else None)
        agent_mode = "tool_first" if tool_names else "direct"
        nodes.append(
            {
                "id": node_id,
                "type": "workflowNode",
                "position": {"x": 360 * index, "y": 120 + ((index - 1) % 3) * 170},
                "data": {
                    "kind": "agent",
                    "title": _title_from_name(task.name),
                    "description": task.description,
                    "agentMode": agent_mode,
                    "instruction": instruction,
                    "modelId": model_id,
                    "toolNames": ", ".join(tool_names),
                    "outputVariable": output_name,
                    "maxIterations": "5",
                    "temperature": "0.7",
                    "promptSuffix": "",
                },
            }
        )

    edges: list[dict[str, Any]] = []
    for task in ordered_tasks:
        if not incoming[task.name]:
            edges.append(_edge("input_goal", task_ids[task.name]))

    for source, target in task_edges:
        edges.append(_edge(task_ids[source], task_ids[target]))

    terminal_tasks = [task for task in ordered_tasks if not outgoing[task.name]]
    if len(terminal_tasks) > 1:
        aggregate_id = "aggregate_meta_agent_outputs"
        terminal_variables = [
            task_output_variables[task.name]
            for task in terminal_tasks
            if task.name in task_output_variables
        ]
        nodes.append(
            {
                "id": aggregate_id,
                "type": "workflowNode",
                "position": {"x": 360 * (len(ordered_tasks) + 1), "y": 120},
                "data": {
                    "kind": "variable_aggregator",
                    "title": "Aggregate terminal outputs",
                    "description": "Combines outputs from terminal tasks.",
                    "variableNames": ", ".join(terminal_variables),
                    "outputTemplate": "## {name}\n{value}\n\n",
                    "outputVariable": "meta_agent_result",
                },
            }
        )
        for task in terminal_tasks:
            edges.append(_edge(task_ids[task.name], aggregate_id))
        final_output_variable = "meta_agent_result"
        output_source_id = aggregate_id
        output_x = 360 * (len(ordered_tasks) + 2)
    else:
        terminal = terminal_tasks[0] if terminal_tasks else ordered_tasks[-1]
        final_output_variable = task_output_variables.get(terminal.name, "goal")
        output_source_id = task_ids[terminal.name]
        output_x = 360 * (len(ordered_tasks) + 1)

    nodes.append(
        {
            "id": "output_final",
            "type": "workflowNode",
            "position": {"x": output_x, "y": 120},
            "data": {
                "kind": "output",
                "title": "Final delivery",
                "description": "The final result of the generated meta-agent workflow.",
                "outputVariable": final_output_variable,
            },
        }
    )
    edges.append(_edge(output_source_id, "output_final"))

    return (
        {
            "id": "meta-agent-draft",
            "title": _workflow_title(goal),
            "nodes": nodes,
            "edges": _dedupe_edges(edges),
            "updatedAt": "",
        },
        warnings,
    )


def _normalize_task_payload(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("Each sub_task must be a JSON object.")
    normalized = dict(item)
    if "inputs" not in normalized:
        normalized["inputs"] = []
    if "outputs" not in normalized:
        normalized["outputs"] = []
    if "agent" not in normalized and isinstance(normalized.get("agents"), list):
        agents = normalized["agents"]
        normalized["agent"] = agents[0] if agents else None
    return normalized


def _unique_task_ids(tasks: list[MetaAgentSubTask]) -> dict[str, str]:
    counts: dict[str, int] = defaultdict(int)
    ids: dict[str, str] = {}
    for index, task in enumerate(tasks, start=1):
        base = _safe_identifier(task.name, f"task_{index}")
        counts[base] += 1
        ids[task.name] = base if counts[base] == 1 else f"{base}_{counts[base]}"
    return ids


def _safe_identifier(value: str, fallback: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or not re.match(r"^[a-z_]", text):
        text = fallback
    return text[:80]


def _topological_tasks(
    tasks: list[MetaAgentSubTask],
    edges: list[tuple[str, str]],
) -> list[MetaAgentSubTask]:
    by_name = {task.name: task for task in tasks}
    indegree = {task.name: 0 for task in tasks}
    children: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if source not in by_name or target not in by_name:
            continue
        indegree[target] += 1
        children[source].append(target)

    queue = deque([task.name for task in tasks if indegree[task.name] == 0])
    ordered: list[str] = []
    while queue:
        name = queue.popleft()
        ordered.append(name)
        for child in children[name]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(ordered) != len(tasks):
        return tasks
    return [by_name[name] for name in ordered]


def _primary_agent(task: MetaAgentSubTask) -> MetaAgentGeneratedAgent | None:
    if task.agent:
        return task.agent
    if task.agents:
        return task.agents[0]
    return None


def _agent_prompt(
    task: MetaAgentSubTask,
    agent: MetaAgentGeneratedAgent | None,
) -> str:
    if agent and agent.prompt.strip():
        return agent.prompt.strip()

    input_lines = "\n".join(
        f"- {param.name}: <input>{{{param.name}}}</input>" for param in task.inputs
    )
    output_lines = "\n".join(
        f"## {param.name}\n{param.description or 'Task output.'}"
        for param in task.outputs
    )
    fallback_input = "- goal: <input>{goal}</input>"
    return (
        f"### Objective\n{task.description}\n\n"
        "### Instructions\n"
        f"Use these inputs to complete the task:\n{input_lines or fallback_input}\n\n"
        f"### Output Format\n{output_lines}"
    )


def _convert_placeholders(instruction: str, aliases: dict[str, str]) -> str:
    converted = instruction
    for original, safe in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        original_pattern = re.escape(original)
        converted = re.sub(
            rf"<input>\s*\{{\s*{original_pattern}\s*\}}\s*</input>",
            f"{{{{{safe}}}}}",
            converted,
        )
        converted = re.sub(
            rf"(?<!\{{)\{{\s*{original_pattern}\s*\}}(?!\}})",
            f"{{{{{safe}}}}}",
            converted,
        )
    return converted


def _repair_unbound_template_variables(
    *,
    instruction: str,
    allowed: set[str],
    fallback: str,
    warnings: list[str],
    task_name: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name in allowed or name == fallback:
            return match.group(0)
        warnings.append(
            f"Task {task_name} referenced unbound template variable {name}; "
            f"falling back to {fallback}."
        )
        return f"{{{{{fallback}}}}}"

    return re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", replace, instruction)


def _clean_tool_names(tool_names: list[str] | None) -> list[str]:
    if not tool_names:
        return []
    return [
        item.strip()
        for item in tool_names
        if isinstance(item, str) and item.strip()
    ][:8]


def _title_from_name(name: str) -> str:
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Agent task"


def _workflow_title(goal: str) -> str:
    compact = re.sub(r"\s+", " ", goal.strip())
    return f"Meta-agent draft: {compact[:40]}"


def _edge(source: str, target: str) -> dict[str, Any]:
    return {
        "id": f"edge-{source}-{target}",
        "source": source,
        "target": target,
        "className": "modelmirror-workflow-edge",
        "animated": True,
    }


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for edge in edges:
        key = (str(edge["source"]), str(edge["target"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique
