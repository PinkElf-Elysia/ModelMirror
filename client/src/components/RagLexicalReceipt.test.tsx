import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import RagLexicalReceipt from "./RagLexicalReceipt";

describe("RAG lexical query receipt", () => {
  it("shows bounded counts and the saturation caveat without raw identifiers", () => {
    render(<RagLexicalReceipt receipt={{
      contract_version: "sqlite-fts5-lexical-v2",
      query_policy: "minimum_should_match_auto_v1",
      candidate_limit: 40, initial_candidate_count: 40,
      minimum_should_match_count: 3, final_count: 3,
      effective_term_count: 4, required_term_count: 2,
      candidate_pool_saturated: true,
      raw_query: "private-query", identifier: "SECRET-ID-42",
    }} />);
    expect(screen.getByText(/初始候选 40\/40/)).toBeInTheDocument();
    expect(screen.getByText(/至少命中 2\/4/)).toBeInTheDocument();
    expect(screen.getByText(/规则通过 3/)).toBeInTheDocument();
    expect(screen.getByText(/空结果不代表完整语料没有答案/)).toBeInTheDocument();
    expect(screen.queryByText(/private-query|SECRET-ID-42/)).not.toBeInTheDocument();
  });

  it("keeps old receipts readable and rejects malformed counts", () => {
    const { rerender } = render(<RagLexicalReceipt receipt={{ contract_version: "sqlite-fts5-lexical-v1" }} />);
    expect(screen.getByText(/历史全文合同/)).toBeInTheDocument();
    rerender(<RagLexicalReceipt receipt={{ contract_version: "sqlite-fts5-lexical-v2", initial_candidate_count: "private-text" }} />);
    expect(screen.getByText(/全文回执不完整/)).toBeInTheDocument();
    expect(screen.queryByText(/private-text/)).not.toBeInTheDocument();
  });
});
