import { describe, expect, it } from "vitest";

import {
  authoringDiffSummary,
  buildMetadataPatch,
  canUseTypedHeadlessAuthoring,
  headlessStateMode,
  normalizeGraphPatchEnvelope,
  normalizeGraphPatchPreview,
  normalizeHeadlessProposalState,
} from "./metaAuthoring";

describe("Meta Planner headless authoring contracts", () => {
  it("normalizes a V3 proposal state without inventing resource authority", () => {
    const state = normalizeHeadlessProposalState({
      proposal_id: "proposal_v3",
      proposal_revision: 4,
      authoring_protocol_version: "graph-patch-v1",
      ir_version: 3,
      can_author: true,
      graph_checksum: "graph-checksum",
      candidate_checksum: "candidate-checksum",
      allowed_node_kinds: [
        "input",
        "output",
        "workflow_agent",
        "json_serialize",
        "json_deserialize",
        "variable_aggregator",
        "data_aggregate",
        "dataset_compare",
      ],
      compiler_managed_node_kinds: ["input", "output"],
      authorized_scope: {
        agent_ids: ["expert-reviewer"],
      },
      compatibility: { source_version: 3, lossy: false },
    });

    expect(state).toMatchObject({
      proposal_id: "proposal_v3",
      proposal_revision: 4,
      can_author: true,
      allowed_node_kinds: [
        "input",
        "output",
        "workflow_agent",
        "json_serialize",
        "json_deserialize",
        "variable_aggregator",
        "data_aggregate",
        "dataset_compare",
      ],
      allowed_source_agent_ids: ["expert-reviewer"],
    });
    expect(state?.allowed_node_kinds).toContain("json_serialize");
    expect(state?.allowed_node_kinds).not.toContain("knowledge_retrieval");
  });

  it("allows a lossless V2 proposal to upgrade through typed apply", () => {
    const state = normalizeHeadlessProposalState({
      proposal_id: "proposal_v2",
      proposal_revision: 2,
      authoring_protocol_version: 1,
      ir_version: 2,
      can_author: true,
      graph_checksum: "graph-v2",
      candidate_checksum: "candidate-v2",
      compatibility: {
        source_version: 2,
        upgraded: true,
        lossy: false,
      },
    });

    expect(state).not.toBeNull();
    expect(canUseTypedHeadlessAuthoring(state!)).toBe(true);
    expect(
      canUseTypedHeadlessAuthoring({
        ...state!,
        compatibility: { ...state!.compatibility, lossy: true },
      }),
    ).toBe(false);
  });

  it("never downgrades a server-denied typed state to legacy authoring", () => {
    const state = normalizeHeadlessProposalState({
      proposal_id: "proposal_denied",
      proposal_revision: 2,
      authoring_protocol_version: 1,
      ir_version: 3,
      can_author: false,
      graph_checksum: "graph-checksum",
      candidate_checksum: "candidate-checksum",
      compatibility: { source_version: 3, lossy: false },
    });

    expect(state).not.toBeNull();
    expect(headlessStateMode(state!)).toBe("unavailable");
    expect(canUseTypedHeadlessAuthoring(state!)).toBe(false);
  });

  it("rejects malformed editor diff envelopes before preview", () => {
    expect(normalizeGraphPatchEnvelope({
      proposal_revision: 2,
      expected_graph_checksum: "graph",
      operations: [{ op: "add_node" }],
    })).toBeNull();
    expect(normalizeGraphPatchEnvelope({
      proposal_revision: 2,
      expected_graph_checksum: "graph",
      expected_candidate_checksum: "candidate",
      operations: Array.from({ length: 65 }, () => ({ op: "move_node" })),
    })).toBeNull();
    expect(normalizeGraphPatchEnvelope({
      protocol_version: 2,
      proposal_revision: 2,
      expected_graph_checksum: "graph",
      expected_candidate_checksum: "candidate",
      operations: [{ op: "move_node" }],
    })).toBeNull();
    expect(normalizeGraphPatchEnvelope({
      protocol_version: 1,
      proposal_revision: 2,
      expected_graph_checksum: "graph",
      expected_candidate_checksum: "candidate",
      operations: [{ op: "move_node" }, { injected: true }],
    })).toBeNull();
  });

  it("builds metadata edits as a revision and checksum bound patch", () => {
    const state = normalizeHeadlessProposalState({
      proposal_id: "proposal_v3",
      proposal_revision: 7,
      protocol_version: 1,
      ir_version: 3,
      graph_ir_checksum: "graph-v7",
      compiled_candidate_checksum: "candidate-v7",
      allowed_node_kinds: ["workflow_agent"],
      compatibility: { lossy: false },
    });
    expect(state).not.toBeNull();
    const patch = buildMetadataPatch(state!, {
      name: "Research Xpert",
      description: "Bounded research workflow",
      tags: ["research"],
      starters: [],
    });
    expect(patch).toEqual({
      protocol_version: 1,
      proposal_revision: 7,
      expected_graph_checksum: "graph-v7",
      expected_candidate_checksum: "candidate-v7",
      operations: [{
        op: "set_xpert_metadata",
        name: "Research Xpert",
        description: "Bounded research workflow",
        tags: ["research"],
        starters: [],
      }],
    });
  });

  it("keeps failed preview diagnostics visible and non-applicable", () => {
    const preview = normalizeGraphPatchPreview({
      preview_checksum: "preview-1",
      can_apply: false,
      diagnostics: [{ code: "TYPE_MISMATCH", message: "Port types differ" }],
      warnings: ["snapshot drift"],
      diff: { nodes_updated: ["agent-a"] },
    });
    expect(preview?.can_apply).toBe(false);
    expect(preview?.diagnostics[0]?.code).toBe("TYPE_MISMATCH");
    expect(authoringDiffSummary(preview?.diff ?? {})).toContain("nodes_updated: 1");
  });
});
