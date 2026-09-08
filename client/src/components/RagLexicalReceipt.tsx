/** Only bounded technical counts are displayed; never echo query/identifier text. */
export default function RagLexicalReceipt({ receipt }: { receipt: unknown }) {
  if (!receipt || typeof receipt !== "object") return null;
  const value = receipt as Record<string, unknown>;
  if (value.contract_version === "sqlite-fts5-lexical-v1") {
    return <p className="mt-2 text-xs text-amber-200">历史全文合同：保留原查询规则，仅供兼容读取。</p>;
  }
  const keys = ["candidate_limit", "initial_candidate_count", "minimum_should_match_count", "final_count", "effective_term_count", "required_term_count"] as const;
  if (value.contract_version !== "sqlite-fts5-lexical-v2"
    || value.query_policy !== "minimum_should_match_auto_v1"
    || keys.some((key) => typeof value[key] !== "number" || !Number.isInteger(value[key]) || (value[key] as number) < 0 || (value[key] as number) > 500)) {
    return Object.keys(value).length ? <p className="mt-2 text-xs text-amber-200">全文回执不完整，不能据此判断查询规则是否执行。</p> : null;
  }
  const count = (key: typeof keys[number]) => value[key] as number;
  return (
    <div className="mt-2 text-xs leading-5 text-slate-200" aria-label="全文检索回执">
      <p>全文 V2 · 初始候选 {count("initial_candidate_count")}/{count("candidate_limit")} · 普通词至少命中 {count("required_term_count")}/{count("effective_term_count")} · 规则通过 {count("minimum_should_match_count")} · 送入后续排序 {count("final_count")}</p>
      {value.candidate_pool_saturated === true ? <p className="text-amber-200">候选池已达上限；空结果不代表完整语料没有答案。</p> : null}
    </div>
  );
}
