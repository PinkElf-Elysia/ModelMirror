"""Fixture-only conversion; production never infers structured flow from a DAG."""
import re


def step(ref, branches=None):
    result = {"type": "node", "node_ref": ref}
    if branches is not None:
        result["branches"] = [{"outcome_ref": outcome, "steps": steps} for outcome, steps in branches.items()]
    return result


def from_intent(graph, flow=None):
    result = graph.model_dump(mode="json", exclude={"control_edges"})
    result["generation_protocol_version"] = 1
    result["control_flow"] = flow or [step(node.ref) for node in graph.nodes]
    for node in result["nodes"]:
        mapping = {item["variable"]: f"{item['source_ref']}.{item['source_port']}" for item in node["inputs"]}
        if node["kind"] == "workflow_agent":
            for field in ("role_prompt", "task_input"):
                node["config"][field] = re.sub(r"\{\{\s*(.*?)\s*\}\}",
                    lambda match: "{{" + mapping[match.group(1).strip()] + "}}", node["config"][field])
        node.pop("outputs")
        for binding in node["inputs"]:
            binding.pop("variable")
            binding.pop("value_schema")
    return result
