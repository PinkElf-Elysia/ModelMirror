from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


GRAPH_VERSION = "knowledge-pipeline-graph-v1"

NODE_SPECS: dict[str, dict[str, Any]] = {
    "data_source": {
        "title": "数据源",
        "input": None,
        "output": "documents",
        "stage": "source",
    },
    "structured_processor": {
        "title": "结构化处理器",
        "input": "documents",
        "output": "blocks",
        "stage": "processor",
    },
    "recursive_chunker": {
        "title": "递归分块器",
        "input": "blocks",
        "output": "chunks",
        "stage": "chunker",
    },
    "parent_child_chunker": {
        "title": "父子分块器",
        "input": "blocks",
        "output": "chunks",
        "stage": "chunker",
    },
    "embedding": {
        "title": "Embedding",
        "input": "chunks",
        "output": "embeddings",
        "stage": "embedding",
    },
    "dual_index": {
        "title": "双索引",
        "input": "embeddings",
        "output": "index",
        "stage": "index",
    },
    "retrieval": {
        "title": "检索配置",
        "input": "index",
        "output": None,
        "stage": "retrieval",
    },
    "image_understanding": {
        "title": "图像理解",
        "input": "documents",
        "output": "documents",
        "stage": "vision",
        "available": True,
    },
}

REQUIRED_STAGES = ("source", "processor", "chunker", "embedding", "index", "retrieval")
OPTIONAL_STAGES = ("vision",)


@dataclass(frozen=True, slots=True)
class GraphValidationIssue:
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "node_id": self.node_id,
            "edge_id": self.edge_id,
        }


@dataclass(frozen=True, slots=True)
class KnowledgePipelineCompileResult:
    graph: dict[str, Any]
    stage_updates: dict[str, dict[str, Any]]
    embedding_profile: dict[str, Any]
    retrieval_profile: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "stage_updates": copy.deepcopy(self.stage_updates),
            "embedding_profile": copy.deepcopy(self.embedding_profile),
            "retrieval_profile": copy.deepcopy(self.retrieval_profile),
        }


class PipelineGraphValidationError(ValueError):
    def __init__(self, issues: list[GraphValidationIssue]) -> None:
        self.issues = issues
        super().__init__(issues[0].message if issues else "Knowledge pipeline graph is invalid.")


def default_pipeline_graph(kb_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    stages = draft.get("stages") if isinstance(draft.get("stages"), dict) else {}
    source_config = dict(stages.get("stage_data_source") or {})
    processor_config = dict(stages.get("stage_processor") or {})
    vision_config = dict(stages.get("stage_image_understanding") or {})
    chunker_config = dict(stages.get("stage_chunker") or {})
    strategy = str(chunker_config.get("strategy") or "recursive_character")
    chunker_kind = (
        "parent_child_chunker" if strategy == "parent_child" else "recursive_chunker"
    )
    vision_enabled = bool(vision_config.get("enabled", False))
    positions = {
        "source": {"x": 40.0, "y": 180.0},
        "vision": {"x": 260.0, "y": 180.0},
        "processor": {"x": 480.0 if vision_enabled else 300.0, "y": 180.0},
        "chunker": {"x": 740.0 if vision_enabled else 560.0, "y": 180.0},
        "embedding": {"x": 1000.0 if vision_enabled else 820.0, "y": 180.0},
        "index": {"x": 1260.0 if vision_enabled else 1080.0, "y": 180.0},
        "retrieval": {"x": 1520.0 if vision_enabled else 1340.0, "y": 180.0},
    }
    nodes = [_node("source", "data_source", positions["source"], source_config)]
    if vision_enabled:
        nodes.append(
            _node("vision", "image_understanding", positions["vision"], vision_config)
        )
    nodes.extend([
        _node("processor", "structured_processor", positions["processor"], processor_config),
        _node("chunker", chunker_kind, positions["chunker"], chunker_config),
        _node(
            "embedding",
            "embedding",
            positions["embedding"],
            dict(draft.get("embedding_profile") or {}),
        ),
        _node(
            "index",
            "dual_index",
            positions["index"],
            {"vector_enabled": True, "fulltext_enabled": True},
        ),
        _node(
            "retrieval",
            "retrieval",
            positions["retrieval"],
            dict(draft.get("retrieval_profile") or {}),
        ),
    ])
    node_by_id = {str(node["id"]): node for node in nodes}
    edges = []
    pairs = [
        ("source", "vision") if vision_enabled else ("source", "processor"),
    ]
    if vision_enabled:
        pairs.append(("vision", "processor"))
    pairs.extend((
        ("processor", "chunker"),
        ("chunker", "embedding"),
        ("embedding", "index"),
        ("index", "retrieval"),
    ))
    for source_id, target_id in pairs:
        source = node_by_id[source_id]
        target = node_by_id[target_id]
        edges.append(
            {
                "id": f"edge_{source_id}_{target_id}",
                "source": source_id,
                "target": target_id,
                "source_port": NODE_SPECS[str(source["kind"])]["output"],
                "target_port": NODE_SPECS[str(target["kind"])]["input"],
            }
        )
    return {
        "version": GRAPH_VERSION,
        "kb_id": kb_id,
        "nodes": nodes,
        "edges": edges,
    }


def sync_graph_from_draft(
    graph: dict[str, Any],
    draft: dict[str, Any],
    *,
    kb_id: str,
) -> dict[str, Any]:
    default = default_pipeline_graph(kb_id, draft)
    existing_nodes = {
        str(item.get("id")): item
        for item in graph.get("nodes", [])
        if isinstance(item, dict) and item.get("id")
    }
    for node in default["nodes"]:
        existing = existing_nodes.get(str(node["id"]))
        if not isinstance(existing, dict):
            continue
        position = existing.get("position")
        if isinstance(position, dict):
            node["position"] = {
                "x": float(position.get("x", node["position"]["x"])),
                "y": float(position.get("y", node["position"]["y"])),
            }
    return default


def validate_pipeline_graph(graph: dict[str, Any]) -> list[GraphValidationIssue]:
    issues: list[GraphValidationIssue] = []
    if not isinstance(graph, dict):
        return [GraphValidationIssue("invalid_graph", "Graph must be an object.")]
    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    if not isinstance(raw_nodes, list):
        issues.append(GraphValidationIssue("invalid_nodes", "Graph nodes must be a list."))
        raw_nodes = []
    if not isinstance(raw_edges, list):
        issues.append(GraphValidationIssue("invalid_edges", "Graph edges must be a list."))
        raw_edges = []

    nodes: dict[str, dict[str, Any]] = {}
    stage_nodes: dict[str, list[str]] = {
        stage: [] for stage in (*REQUIRED_STAGES, *OPTIONAL_STAGES)
    }
    enabled_nodes: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            issues.append(GraphValidationIssue("invalid_node", f"Node {index} must be an object."))
            continue
        node_id = str(raw_node.get("id") or "").strip()
        kind = str(raw_node.get("kind") or "").strip()
        if not node_id:
            issues.append(GraphValidationIssue("missing_node_id", "Every node needs an id."))
            continue
        if node_id in nodes:
            issues.append(GraphValidationIssue("duplicate_node_id", f"Duplicate node id: {node_id}", node_id=node_id))
            continue
        if kind not in NODE_SPECS:
            issues.append(GraphValidationIssue("unsupported_node_kind", f"Unsupported node kind: {kind or '(empty)'}", node_id=node_id))
            continue
        nodes[node_id] = raw_node
        enabled = raw_node.get("enabled", True)
        if not isinstance(enabled, bool):
            issues.append(GraphValidationIssue("invalid_node_enabled", "Node enabled must be boolean.", node_id=node_id))
            continue
        spec = NODE_SPECS[kind]
        if spec.get("available") is False and enabled:
            issues.append(GraphValidationIssue("node_not_available", f"{spec['title']} is a disabled placeholder and cannot execute.", node_id=node_id))
        if enabled:
            enabled_nodes.add(node_id)
            stage = str(spec["stage"])
            if stage in stage_nodes:
                stage_nodes[stage].append(node_id)
        position = raw_node.get("position")
        if not isinstance(position, dict):
            issues.append(GraphValidationIssue("invalid_position", "Node position must be an object.", node_id=node_id))

    for stage in REQUIRED_STAGES:
        ids = stage_nodes[stage]
        if not ids:
            issues.append(GraphValidationIssue("missing_required_stage", f"Graph needs one enabled {stage} node."))
        elif len(ids) > 1:
            issues.append(GraphValidationIssue("duplicate_stage", f"Graph allows only one enabled {stage} node."))
    for stage in OPTIONAL_STAGES:
        if len(stage_nodes[stage]) > 1:
            issues.append(GraphValidationIssue("duplicate_stage", f"Graph allows only one enabled {stage} node."))

    ordered_stages = ["source"]
    if stage_nodes["vision"]:
        ordered_stages.append("vision")
    ordered_stages.extend(("processor", "chunker", "embedding", "index", "retrieval"))
    expected_stage_pairs = set(zip(ordered_stages, ordered_stages[1:]))

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in enabled_nodes}
    incoming: dict[str, set[str]] = {node_id: set() for node_id in enabled_nodes}
    edge_pairs: set[tuple[str, str]] = set()
    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            issues.append(GraphValidationIssue("invalid_edge", f"Edge {index} must be an object."))
            continue
        edge_id = str(raw_edge.get("id") or f"edge_{index}")
        source_id = str(raw_edge.get("source") or "")
        target_id = str(raw_edge.get("target") or "")
        if source_id not in nodes or target_id not in nodes:
            issues.append(GraphValidationIssue("edge_node_missing", "Edge source and target must exist.", edge_id=edge_id))
            continue
        if source_id == target_id:
            issues.append(GraphValidationIssue("self_edge", "A node cannot connect to itself.", node_id=source_id, edge_id=edge_id))
            continue
        if source_id not in enabled_nodes or target_id not in enabled_nodes:
            issues.append(GraphValidationIssue("disabled_node_edge", "Edges may only connect enabled nodes.", edge_id=edge_id))
            continue
        source_kind = str(nodes[source_id]["kind"])
        target_kind = str(nodes[target_id]["kind"])
        expected_source = NODE_SPECS[source_kind]["output"]
        expected_target = NODE_SPECS[target_kind]["input"]
        source_port = raw_edge.get("source_port", raw_edge.get("sourceHandle"))
        target_port = raw_edge.get("target_port", raw_edge.get("targetHandle"))
        if source_port != expected_source or target_port != expected_target:
            issues.append(GraphValidationIssue("invalid_edge_port", f"Edge {edge_id} does not match node ports.", edge_id=edge_id))
        source_stage = str(NODE_SPECS[source_kind]["stage"])
        target_stage = str(NODE_SPECS[target_kind]["stage"])
        if (source_stage, target_stage) not in expected_stage_pairs:
            issues.append(GraphValidationIssue("invalid_stage_order", "Edges must follow source -> optional vision -> processor -> chunker -> embedding -> index -> retrieval.", edge_id=edge_id))
        if (source_id, target_id) in edge_pairs:
            issues.append(GraphValidationIssue("duplicate_edge", "Duplicate graph edge.", edge_id=edge_id))
        edge_pairs.add((source_id, target_id))
        adjacency.setdefault(source_id, set()).add(target_id)
        incoming.setdefault(target_id, set()).add(source_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for target in adjacency.get(node_id, set()):
            if visit(target):
                return True
        visiting.remove(node_id)
        visited.add(node_id)
        return False

    if any(visit(node_id) for node_id in list(enabled_nodes) if node_id not in visited):
        issues.append(GraphValidationIssue("graph_cycle", "Knowledge pipeline graph must be acyclic."))

    if all(len(stage_nodes[stage]) == 1 for stage in REQUIRED_STAGES):
        ordered = [stage_nodes[stage][0] for stage in ordered_stages]
        expected_pairs = set(zip(ordered, ordered[1:]))
        if edge_pairs != expected_pairs:
            issues.append(GraphValidationIssue("incomplete_chain", "Enabled nodes must form one complete pipeline chain."))
        for node_id in ordered:
            if node_id == ordered[0]:
                if incoming.get(node_id):
                    issues.append(GraphValidationIssue("source_has_input", "Data source cannot have an incoming edge.", node_id=node_id))
            elif len(incoming.get(node_id, set())) != 1:
                issues.append(GraphValidationIssue("invalid_incoming_edges", "Each stage needs exactly one upstream edge.", node_id=node_id))
            if node_id == ordered[-1]:
                if adjacency.get(node_id):
                    issues.append(GraphValidationIssue("retrieval_has_output", "Retrieval cannot have an outgoing edge.", node_id=node_id))
            elif len(adjacency.get(node_id, set())) != 1:
                issues.append(GraphValidationIssue("invalid_outgoing_edges", "Each stage needs exactly one downstream edge.", node_id=node_id))

    return _dedupe_issues(issues)


def compile_pipeline_graph(graph: dict[str, Any]) -> KnowledgePipelineCompileResult:
    issues = validate_pipeline_graph(graph)
    if issues:
        raise PipelineGraphValidationError(issues)
    nodes = [item for item in graph["nodes"] if item.get("enabled", True)]
    by_stage = {
        str(NODE_SPECS[str(node["kind"])]["stage"]): node
        for node in nodes
    }
    source = by_stage["source"]
    processor = by_stage["processor"]
    vision = by_stage.get("vision")
    chunker = by_stage["chunker"]
    embedding = by_stage["embedding"]
    index = by_stage["index"]
    retrieval = by_stage["retrieval"]
    index_config = dict(index.get("config") or {})
    if index_config.get("vector_enabled", True) is not True or index_config.get("fulltext_enabled", True) is not True:
        raise PipelineGraphValidationError(
            [GraphValidationIssue("dual_index_required", "Both vector and full-text indexes must stay enabled.", node_id=str(index["id"]))]
        )
    chunker_config = dict(chunker.get("config") or {})
    chunker_config["strategy"] = (
        "parent_child"
        if str(chunker["kind"]) == "parent_child_chunker"
        else "recursive_character"
    )
    normalized = {
        "version": GRAPH_VERSION,
        "kb_id": str(graph.get("kb_id") or ""),
        "nodes": [_normalized_node(item) for item in graph["nodes"]],
        "edges": [_normalized_edge(item) for item in graph["edges"]],
    }
    return KnowledgePipelineCompileResult(
        graph=normalized,
        stage_updates={
            "stage_data_source": dict(source.get("config") or {}),
            "stage_processor": dict(processor.get("config") or {}),
            "stage_chunker": chunker_config,
            "stage_image_understanding": (
                {
                    **dict(vision.get("config") or {}),
                    "enabled": True,
                }
                if vision is not None
                else {"enabled": False, "provider": "openai_compatible_vlm"}
            ),
        },
        embedding_profile={
            "model": str((embedding.get("config") or {}).get("model") or "").strip()
        },
        retrieval_profile=dict(retrieval.get("config") or {}),
    )


def _node(node_id: str, kind: str, position: dict[str, float], config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "title": NODE_SPECS[kind]["title"],
        "position": position,
        "config": copy.deepcopy(config),
        "enabled": True,
    }


def _normalized_node(node: dict[str, Any]) -> dict[str, Any]:
    position = node.get("position") if isinstance(node.get("position"), dict) else {}
    kind = str(node["kind"])
    return {
        "id": str(node["id"]),
        "kind": kind,
        "title": str(node.get("title") or NODE_SPECS[kind]["title"])[:120],
        "position": {
            "x": float(position.get("x", 0)),
            "y": float(position.get("y", 0)),
        },
        "config": copy.deepcopy(node.get("config") if isinstance(node.get("config"), dict) else {}),
        "enabled": bool(node.get("enabled", True)),
    }


def _normalized_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(edge.get("id") or ""),
        "source": str(edge["source"]),
        "target": str(edge["target"]),
        "source_port": edge.get("source_port", edge.get("sourceHandle")),
        "target_port": edge.get("target_port", edge.get("targetHandle")),
    }


def _dedupe_issues(issues: list[GraphValidationIssue]) -> list[GraphValidationIssue]:
    result: list[GraphValidationIssue] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for issue in issues:
        key = (issue.code, issue.node_id, issue.edge_id)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
