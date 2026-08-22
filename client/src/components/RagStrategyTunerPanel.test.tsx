import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RagStrategyTunerPanel from "./RagStrategyTunerPanel";

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const readiness = {
  status: "ready",
  benchmark_role: "strategy_tuning",
  selection_eligible: true,
  evidence_strength: "qualified",
  counts: {
    total: 42,
    positive: 30,
    no_result: 12,
    reviewed_hard_negative: 12,
  },
  dimensions: {
    retrieval: { eligible: true },
    threshold: { eligible: true },
    chunking: { eligible: true, sensitivity_probe_required: false },
  },
  checks: [],
  blockers: [],
  warnings: [],
};

afterEach(() => vi.restoreAllMocks());

describe("RagStrategyTunerPanel run scope", () => {
  it("defaults visibly to optimization-only and sends the selected boundary", async () => {
    const preflightBodies: Array<Record<string, unknown>> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/rag/pipeline/versions?kb_id=kb-fixed") {
        return json({
          versions: [
            {
              version_id: "kpv-fixed",
              version: 3,
              status: "ready",
              active: false,
              chunk_count: 12,
            },
          ],
        });
      }
      if (url === "/api/rag/evaluation-sets?kb_id=kb-fixed") {
        return json({
          evaluation_sets: [
            { eval_set_id: "eval-fixed", name: "Qualified Gold", latest_version: 2 },
          ],
        });
      }
      if (url === "/api/rag/strategy-router/recommendations?kb_id=kb-fixed") {
        return json({ recommendations: [] });
      }
      if (url === "/api/rag/evaluation-sets/eval-fixed/versions") {
        return json({
          versions: [
            {
              version_id: "eval-fixed-v2",
              version: 2,
              cases: Array.from({ length: 42 }),
              checksum: "fixed-checksum",
            },
          ],
        });
      }
      if (url === "/api/rag/strategy-tuner/preflight" && init?.method === "POST") {
        preflightBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return json({
          snapshot_hash: "snapshot-fixed",
          run_scope: preflightBodies.at(-1)?.run_scope,
          eval_case_count: 42,
          chunk_tuning_available: true,
          threshold_tuning_available: true,
          benchmark_role: "strategy_tuning",
          selection_eligible: true,
          tuning_readiness: readiness,
          retrieval_only: false,
          embedding_degraded: false,
          rerank_available: true,
          warnings: [],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(
      <RagStrategyTunerPanel
        kbId="kb-fixed"
        onClose={vi.fn()}
        onCompleted={vi.fn()}
        open
      />,
    );

    const optimizationOnly = screen.getByRole("radio", { name: /仅优化集/ });
    const full = screen.getByRole("radio", { name: /完整调优/ });
    const rerank = screen.getByRole("checkbox", { name: /显式授权 finalist Rerank/ });
    expect(optimizationOnly).toBeChecked();
    expect(full).not.toBeChecked();
    expect(rerank).toBeDisabled();
    expect(screen.getByText(/不会执行 Holdout、候选物化或 Formal/)).toBeInTheDocument();

    const preflight = screen.getByRole("button", { name: "运行预检" });
    await waitFor(() => expect(preflight).toBeEnabled());
    fireEvent.click(preflight);
    await waitFor(() => expect(preflightBodies).toHaveLength(1));
    expect(preflightBodies[0]).toMatchObject({
      run_scope: "optimization_only",
      enable_rerank: false,
      max_chunk_indexes: 1,
      max_retrieval_trials: 3,
      max_finalists: 1,
    });

    fireEvent.click(full);
    expect(full).toBeChecked();
    expect(rerank).toBeEnabled();
    fireEvent.click(rerank);
    fireEvent.click(preflight);
    await waitFor(() => expect(preflightBodies).toHaveLength(2));
    expect(preflightBodies[1]).toMatchObject({
      run_scope: "full",
      enable_rerank: true,
    });
  });

  it("binds the preflight snapshot and labels a completed failed gate as a warning", async () => {
    const runBodies: Array<Record<string, unknown>> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/rag/pipeline/versions?kb_id=kb-fixed") {
        return json({
          versions: [{ version_id: "kpv-fixed", version: 3, status: "ready", active: false, chunk_count: 12 }],
        });
      }
      if (url === "/api/rag/evaluation-sets?kb_id=kb-fixed") {
        return json({ evaluation_sets: [{ eval_set_id: "eval-fixed", name: "Qualified Gold", latest_version: 2 }] });
      }
      if (url === "/api/rag/strategy-router/recommendations?kb_id=kb-fixed") return json({ recommendations: [] });
      if (url === "/api/rag/evaluation-sets/eval-fixed/versions") {
        return json({ versions: [{ version_id: "eval-fixed-v2", version: 2, cases: Array.from({ length: 42 }), checksum: "fixed-checksum" }] });
      }
      if (url === "/api/rag/strategy-tuner/preflight" && init?.method === "POST") {
        return json({
          snapshot_hash: "snapshot-fixed",
          run_scope: "optimization_only",
          eval_case_count: 42,
          chunk_tuning_available: true,
          threshold_tuning_available: true,
          benchmark_role: "strategy_tuning",
          selection_eligible: true,
          tuning_readiness: readiness,
          retrieval_only: false,
          embedding_degraded: false,
          rerank_available: true,
          execution_budget: {
            receipt_version: "rag-strategy-execution-budget-v1",
            run_scope: "optimization_only",
            evaluation_case_count: 42,
            optimization_case_count: 28,
            holdout_case_count: 14,
            candidate_profile_upper_bound: 3,
            optimization_query_executions_upper_bound: 84,
            trial_index_builds_upper_bound: 0,
            holdout_query_executions_upper_bound: 0,
            rerank_query_executions_upper_bound: 0,
            formal_query_executions_upper_bound: 0,
            total_retrieval_query_executions_upper_bound: 84,
          },
          warnings: [],
        });
      }
      if (url === "/api/rag/strategy-tuner/runs" && init?.method === "POST") {
        runBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return json({
          run_id: "ragtune-failed",
          request: { run_scope: "optimization_only", enable_rerank: false },
          status: "completed",
          stage: "optimization_completed",
          progress: 100,
          warnings: [],
          finalists: [],
          pareto_front: [],
          candidates: [
            {
              candidate_id: "candidate-vector",
              retrieval: { mode: "vector", top_k: 5 },
              optimization_metrics: { recall_at_5: 0.95, no_result_accuracy: 0, p95_latency_ms: 770 },
              promotion_target_diagnostics: { maximum_no_result_accuracy_at_required_recall: 0.375 },
              optimization_gate: { passed: false },
            },
          ],
          optimization_gate_summary: {
            evaluated_count: 1,
            passed_count: 0,
            eligible_count: 0,
            failed_check_counts: { min_no_result_accuracy: 1 },
          },
          scope_receipt: {
            receipt_version: "rag-strategy-run-scope-v1",
            requested_scope: "optimization_only",
            effective_stop: "after_optimization_gate",
            optimization_candidate_count: 1,
            optimization_eligible_count: 0,
            holdout_query_executions: 0,
            finalist_count: 0,
            materialization_attempted: false,
            evaluation_run_created: false,
          },
          error: null,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<RagStrategyTunerPanel kbId="kb-fixed" onClose={vi.fn()} onCompleted={vi.fn()} open />);
    const preflight = screen.getByRole("button", { name: "运行预检" });
    await waitFor(() => expect(preflight).toBeEnabled());
    fireEvent.click(preflight);
    await screen.findByText(/检索查询执行上限 84 次/);
    fireEvent.click(screen.getByRole("button", { name: "运行优化集" }));

    await screen.findByText("执行完成，质量门禁未通过");
    expect(screen.queryByText("优化集证据已完成")).not.toBeInTheDocument();
    expect(screen.getByText(/min_no_result_accuracy/)).toBeInTheDocument();
    expect(screen.getByText("37.5%")).toBeInTheDocument();
    expect(runBodies[0]).toMatchObject({
      expected_snapshot_hash: "snapshot-fixed",
      max_chunk_indexes: 1,
      max_retrieval_trials: 3,
      max_finalists: 1,
    });
  });
});
