# RAG Strategy Research

> Phase A research artifact for `XPERT-RAG-STRATEGY-ROUTER-01`.
>
> Status: **awaiting user review**. This document does not authorize Phase B implementation.

## 1. Purpose and boundaries

This study asks a narrower question than "which chunking strategy is best":

> Given the corpus shape, retrieval task, available index and cost objective, which **currently supported ModelMirror profile** is a defensible starting point, and when is the evidence too weak to recommend one?

Phase A did not change production code, API, UI, Runtime schema, active indexes, or Pipeline Drafts. It produced:

- a project-authored synthetic fixture set at `docs/research/rag-strategy/fixtures.json`;
- a machine-readable evidence matrix at `docs/research/rag-strategy/evidence-matrix.json`;
- the implementation gap analysis and draft `RAG Strategy Rules V1` in this document.

The local experiment used deterministic hash embeddings and did not call a rerank provider. Therefore it can characterize the offline fallback and exact-term retrieval, but it cannot establish defaults for a real semantic embedding or rerank model.

## 2. Executive conclusions

1. **There is no evidence for a universal winner.** The best aggregate Recall@5 in this small experiment came from recursive 1000/10%, but the lead over the best parent-child profile was only `0.028`; corpus-level results include ties and ranking/recall trade-offs.
2. **Structure preservation should precede size selection.** Markdown headings, tables and code are already parsed into blocks. Splitting is then applied within each block, so many short structured blocks are unaffected by changing 400/700/1000 character limits.
3. **Current parent-child is a selective context-expansion option, not a default.** It indexes child chunks but returns parent text. In this experiment it consumed roughly `1.7x-2.3x` the returned context of comparable recursive profiles without a general recall gain.
4. **Hash vector results are not semantic-vector evidence.** On exact-term-heavy synthetic data, full-text substantially outperformed the deterministic hash vector. Hybrid inherited noise from the weak vector channel. A Router must detect this capability boundary instead of recommending Hybrid by habit.
5. **Overlap has a cost but no universal gain here.** Recursive 700 with 10% and 20% overlap produced the same aggregate quality; 20% indexed about 4.5% more characters. Overlap should be conditional on long blocks and boundary risk.
6. **`score_threshold=0` cannot abstain.** Every no-answer query returned candidates, yielding no-result accuracy `0`. Threshold calibration belongs in the next Benchmark Auto Tuner round, not in deterministic Router V1.
7. **Top-K is a task and cost choice.** Raising Top-K from 5 to 10 did not improve Recall@5 in the selected profiles. Multi-evidence tasks may still need wider candidate pools, but that requires target-specific evaluation.
8. **Semantic chunking, Contextual Retrieval, Late Chunking and RAPTOR remain deferred.** They are meaningful research directions, but ModelMirror does not currently expose compatible execution primitives for them and this experiment did not test them.

## 3. Literature evidence

| Topic | Evidence | Implication for ModelMirror | Classification |
| --- | --- | --- | --- |
| Chunk size and overlap | Azure documents fixed, variable and semantic chunking and notes that overlap and size depend on content; character counts are not token counts. [Azure AI Search](https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-chunk-documents) | Do not represent character limits as model-context tokens. Keep recommendations bounded and explain the unit. | `evidence-backed` |
| Structure-aware chunking | Unstructured chunks document elements after partitioning; `by_title` preserves section boundaries and tables remain separate. It also warns that overlap across complete elements can mix independent units. [Unstructured](https://docs.unstructured.io/open-source/core-functionality/chunking) | Prefer the existing structured Processor for headings, tables and code; only apply overlap to blocks that actually split. | `evidence-backed` |
| Semantic splitting | LlamaIndex groups sentences and uses embedding dissimilarity percentiles to choose breakpoints. [LlamaIndex](https://docs.llamaindex.ai/en/stable/api_reference/node_parsers/semantic_splitter/) | Requires a real embedding model and threshold calibration; hash embedding is not a valid basis. | `literature-only` |
| Exact identifiers and hybrid retrieval | Anthropic notes BM25's value for exact identifiers, combines lexical and embedding retrieval, and measures contextual chunks and reranking against a use-case-specific corpus. [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval) | Exact-term signals should favor full-text. Hybrid and rerank remain evaluation-dependent rather than universal defaults. | `literature-only` plus local support |
| RRF fusion | RRF combines independent ranked lists using reciprocal ranks rather than assuming comparable raw scores. [Elastic](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion) | Current weighted RRF is appropriate as a fusion primitive, but its normalized output is candidate-set-relative. | `evidence-backed` |
| Late Chunking | Late Chunking embeds long context first and derives chunk representations afterwards. [paper](https://arxiv.org/abs/2409.04701) | Needs long-context embedding support and a different indexing path. | `deferred` |
| Hierarchical summaries | RAPTOR recursively clusters and summarizes text for multi-level retrieval. [paper](https://arxiv.org/abs/2401.18059) | Requires generated hierarchy, summary persistence and evaluation beyond current parent-child windows. | `deferred` |
| Evidence sparsity | HiChunk/HiCBench argues that sparse evidence benchmarks can obscure chunking differences. [ACL 2026](https://aclanthology.org/2026.acl-long.1372/) | A strategy benchmark must contain long split-worthy blocks and boundary-sensitive evidence; easy sparse facts are insufficient. | `evidence-backed` |

No published percentage or default from an external study is copied into Rules V1. External results establish dimensions and failure modes; local evidence determines only the limited defaults below.

## 4. Strategy comparison

| Strategy | Strengths | Failure modes and cost | Router V1 status |
| --- | --- | --- | --- |
| Recursive character | Deterministic, inexpensive, offset-preserving, supports custom separators | Character unit differs from tokens; can cut semantic units when blocks are long | Implemented; eligible |
| Structure-aware blocks | Preserves headings, tables, code, lists and PDF pages before splitting | Parser quality bounds results; current block-level split may fragment chapter context | Implemented by Processor; mandatory signal |
| Parent-child | Retrieves focused child and returns broader parent context | Larger context, duplicate parent returns, current implementation cannot form parents across adjacent blocks | Implemented; eligible with low-confidence constraints |
| Sentence window | Keeps citation unit small while expanding neighboring sentences | Requires stable sentence segmentation and window metadata | Not implemented; deferred |
| Semantic chunking | Breakpoints follow semantic change | Embedding and threshold sensitive; higher indexing cost | Research only |
| Contextual Retrieval | Adds document/chunk context before embedding and lexical indexing | Requires generation cost, cache and provenance; may alter exact-term distribution | Research only |
| Late Chunking | Chunk representations retain long-document context | Requires long-context embedding architecture and new index contract | Research only |
| RAPTOR | Supports hierarchical and global questions | Clustering/summarization cost, nondeterminism and complex citation mapping | Research only |

## 5. Current ModelMirror implementation audit

| Area | Current behavior | Router consequence |
| --- | --- | --- |
| Processor | `StructuredDocumentProcessor` emits headings, paragraphs, lists, tables, code and PDF pages with heading/page metadata. | Profile the processed blocks, not only raw file length. |
| Recursive splitter | `TextSplitter` uses character windows, preferred separators and exact source offsets. | Rules and UI must say characters, not tokens. |
| Parent-child splitter | `ParentChildTextSplitter` creates parent windows and child retrieval chunks. Pipeline execution applies it to each processed block. | Parent context cannot cross adjacent blocks; chapter-level hierarchy is not yet present. |
| Full-text | SQLite FTS5/BM25 orders candidates; outward score is rank-normalized. | Strong exact-term candidate; threshold is not a calibrated BM25 score. |
| Vector | Uses configured embeddings; local fallback is deterministic hash embedding. | Router must distinguish semantic-provider readiness from hash fallback. |
| Hybrid | Weighted RRF uses `k=60` and min-max normalizes the current candidate set. | Weights are meaningful within a run; thresholds are not portable across corpora/modes. |
| Rerank | API-first/LLM fallback, fail-open to fused order. | Do not enable without provider readiness and target-specific evaluation. |
| Versioning | Pipeline profiles are pinned to candidate versions with activation and rollback. | Router should only write Draft/Graph; version build remains explicit. |
| Evaluation | RAG evaluation and targeted Gold generation exist. | Next-round tuner can compare candidate profiles without inventing another evaluator. |

### Implementation gaps relevant to strategy selection

1. No deterministic corpus profiler or versioned strategy-rule service exists.
2. Parent-child parents are block-local, not section- or chapter-spanning.
3. No token-aware chunk budget is available; all current split settings are characters.
4. No cross-profile score calibration exists, so threshold recommendation is unsafe.
5. No real-embedding benchmark in Phase A establishes Vector/Hybrid defaults.
6. No rerank provider was approved for this study; effectiveness, latency and cost are unknown.
7. No sentence-window, semantic, contextual, late-chunking or RAPTOR index contract exists.
8. Retry can reuse completed work within a job, but there is no strategy-search cache keyed across independent candidate jobs.

## 6. Local experiment design

### Corpus and Gold

- 17 synthetic Markdown documents across six families.
- 41 fixed Gold queries authored before retrieval.
- Query types: fact, paraphrase, exact term, cross-language, section context, multi-evidence, confusable, table, code and no-answer.
- All corpus families share the same mixed-domain index so retrieval faces distractors.
- Long dense blocks were included specifically to trigger actual splitting; short blocks remain useful for detecting no-op configuration changes.
- Gold anchors were never rewritten based on retrieval outcomes.

### Production logic reused

- Markdown block parsing from `StructuredDocumentProcessor`.
- Pipeline-style per-block splitting and heading-prefix indexing.
- `TextSplitter` and `ParentChildTextSplitter`.
- SQLite FTS5 lexical retrieval.
- Deterministic 384-dimensional hash embeddings.
- Weighted RRF with vector/full-text weights `0.7/0.3`, `k=60`.

The harness only omitted file/network kernel dependencies unavailable in the bundled research Python. It did not replace splitter, block parser, FTS5, hash embedding or RRF behavior.

### Profiles

Stage 1 used Hybrid Top-K 10 for eight chunking profiles:

- Recursive: `400/0`, `400/40`, `700/70`, `700/140`, `1000/100` (`size/overlap`).
- Parent-child: `1200/300`, `1800/450`, `2400/600` (`parent/child`) with approximately 10% parent and child overlap.

Stage 2 selected a representative recursive and parent-child profile per corpus and compared Full-text, Vector and Hybrid with Top-K 5 and 10. Rerank was not tested.

Metrics include Recall@5, MRR@10, NDCG@10, citation/anchor coverage, no-result accuracy, chunk count, indexed/returned characters, index time and retrieval P95. Timings on this tiny corpus are directional only.

## 7. Evidence matrix summary

### 7.1 Chunking profiles

Aggregate over the six corpus families under fixed Hybrid retrieval:

| Profile | Chunks | Recall@5 | MRR@10 | NDCG@10 | Indexed chars | Returned context chars | Index ms | Retrieval P95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Recursive 1000 / 100 | 118 | **0.600** | 0.458 | 0.484 | 14,790 | 14,790 | 67.5 | 10.8 |
| Parent-child 2400 / 600 | 125 | 0.572 | 0.467 | 0.481 | 15,465 | 26,628 | 73.0 | 11.6 |
| Recursive 700 / 70 | 122 | 0.572 | 0.452 | 0.470 | 15,215 | 15,215 | 72.6 | 11.1 |
| Recursive 700 / 140 | 123 | 0.572 | 0.452 | 0.470 | 15,901 | 15,901 | 72.9 | 11.5 |
| Parent-child 1800 / 450 | 130 | 0.544 | **0.468** | **0.485** | 15,835 | 34,114 | 64.7 | 11.8 |
| Recursive 400 / 40 | 131 | 0.544 | 0.467 | 0.484 | 15,863 | 15,863 | 69.1 | 12.6 |
| Recursive 400 / 0 | 130 | 0.517 | 0.438 | 0.462 | 15,163 | 15,163 | 76.2 | 11.8 |
| Parent-child 1200 / 300 | 142 | 0.517 | 0.433 | 0.446 | 16,915 | 34,762 | 75.3 | 12.1 |

Interpretation:

- The recall range is only `0.083`; this is insufficient to declare a universal strategy winner.
- Parent-child 1800/450 has the best MRR/NDCG by very small margins, but returns more than twice the context of recursive 700/70.
- Recursive 700 at 10% and 20% overlap has identical quality. The extra overlap only increased indexed characters in this fixture.
- Smaller chunks increased chunk count but did not generally improve retrieval.

### 7.2 Corpus-level contrasts

| Corpus | Representative recursive | Representative parent-child | Finding |
| --- | --- | --- | --- |
| Long policy | 400/40: R@5 0.833, MRR 0.833 | 1800/450: 0.833, 0.833 | Quality tie; parent-child costs more context. |
| Short FAQ | 400/0: 0.600, 0.622 | 2400/600: 0.600, 0.622 | Most blocks do not split; evidence insufficient. |
| Technical IDs | 1000/100: 0.500, 0.233 | 2400/600: 0.333, 0.357 | Recursive improves recall; parent-child improves ranking. |
| Bilingual narrative | 400/0: 0.333, 0.250 | 2400/600: 0.333, 0.222 | Both weak; hash embedding cannot support cross-language routing. |
| Tables and code | 1000/100: 0.333, 0.333 | 2400/600: 0.333, 0.333 | Tie; preserved structure and lexical signals matter more here. |
| Confusable sections | 1000/100: 1.000, 0.583 | 2400/600: 1.000, 0.547 | Recall tie; recursive ranks slightly better. |

### 7.3 Retrieval modes

Aggregate over the representative profiles:

| Mode | Top-K | Recall@5 | MRR@10 | NDCG@10 | No-result accuracy |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full-text | 5 | **0.800** | 0.737 | 0.730 | 0.000 |
| Full-text | 10 | **0.800** | **0.741** | **0.739** | 0.000 |
| Hybrid | 5 | 0.586 | 0.475 | 0.479 | 0.000 |
| Hybrid | 10 | 0.586 | 0.481 | 0.493 | 0.000 |
| Vector | 5 | 0.461 | 0.377 | 0.376 | 0.000 |
| Vector | 10 | 0.461 | 0.390 | 0.407 | 0.000 |

Interpretation:

- This synthetic corpus contains deliberate exact identifiers, terminology and anchors, which favor FTS5.
- The hash vector channel is weak for paraphrase and cross-language semantics and lowers the fixed 0.7-vector Hybrid result.
- This result **does not** prove that Full-text beats a real semantic embedding. It proves that Router V1 must know whether the vector provider is semantic or hash fallback.
- With threshold `0`, every no-answer query returns candidates. Router V1 must warn rather than pretend to solve abstention.

### 7.4 Evidence sparsity check

An initial pilot used isolated corpus families and split whole documents. Recall saturated near 1.0 and hid strategy differences. That pilot was discarded. The final matrix instead:

- uses production block parsing and per-block splitting;
- adds long split-worthy blocks;
- mixes all 17 documents into the retrieval index;
- de-duplicates overlapping chunks when scoring fixed anchors.

Even after correction, several corpora remain ties. Those ties are reported as `insufficient_data`, not converted into arbitrary rules.

## 8. RAG Strategy Rules V1 draft

Every rule below is deliberately conservative. `Confidence` describes confidence in the recommendation under the listed preconditions, not expected answer quality.

| ID | Signal | Recommendation | Confidence | Counterexample / warning | Basis |
| --- | --- | --- | --- | --- | --- |
| R1 | Most processed blocks are shorter than the current chunk size; low boundary pressure | Keep current profile or use Recursive 700/70 as a neutral editable starting point | Low | Size changes may be no-ops; do not claim improvement | Local EXP-SHORT-01, heuristic default |
| R2 | Many blocks exceed 1,200 chars **and** user selects long-context/quality | Offer Parent-child 1800/450 as an alternative, not automatic winner | Low | Current parents are block-local and may double returned context | Local EXP-LONG-01; literature supports hierarchy, not these exact values |
| R3 | High density of IDs, codes, exact terms, table keys or legal clauses | Recommend Full-text, Top-K 5; show Hybrid as a benchmark-required alternative | Medium | Natural-language paraphrases may need semantic embeddings | Local EXP-LEXICAL-01 plus Anthropic exact-term rationale |
| R4 | Embedding provider is deterministic hash and requirements include semantic rewrite or cross-language | Return `insufficient_data`; do not recommend Vector/Hybrid as quality strategy | High | Full-text may still work for bilingual documents containing both terms | Local EXP-HASH-01 capability boundary |
| R5 | Real semantic embedding is ready and requirements mix exact terms with paraphrase | Offer Hybrid 0.7/0.3 only as a low-confidence starting candidate | Low | Weight is an existing default, not established by this experiment; must benchmark | Literature-only plus existing product default |
| R6 | Long blocks actually split and boundary-sensitive evidence is expected | Start near 10% overlap; do not auto-select 20% | Low-Medium | Lists/tables/code should not overlap across complete blocks | Local EXP-OVERLAP-01 plus Unstructured warning |
| R7 | Table/code ratio is material | Preserve structure; prefer Full-text and a moderate Recursive profile | Medium | Semantic prose around tables can still require Hybrid | Local EXP-STRUCTURE-01 plus structure literature |
| R8 | Confusable sections, quality objective, real rerank provider ready | Recommend evaluating rerank; do not enable automatically in V1 | Low | No local rerank evidence or cost measurement exists | Literature-only; deferred measurement |
| R9 | Low-latency/single-fact objective | Top-K 5 | Low-Medium | Multi-evidence tasks can need a wider candidate pool | Local EXP-TOPK-01 |
| R10 | Multi-evidence or broad chapter context selected | Offer Top-K 10 with context-cost warning | Low | Local Recall@5 did not improve from wider return count | Heuristic; target benchmark required |
| R11 | No-answer/abstention is required while threshold remains 0 | Keep threshold 0 in V1 and emit explicit warning; defer calibration | High | Returning fewer candidates is not equivalent to calibrated abstention | Local EXP-NOANSWER-01 |
| R12 | UI or requirement treats characters as tokens | Emit unit mismatch warning; never convert without tokenizer/model context | High | CJK and English have different character/token ratios | Azure guidance and current splitter contract |
| R13 | User requests semantic chunking, Contextual Retrieval, Late Chunking or RAPTOR | Mark `deferred`, explain missing runtime/index contract | High | Do not simulate advanced strategies with renamed recursive settings | Literature and implementation audit |

### Rule precedence

1. Capability boundaries (`R4`, `R11`, `R12`, `R13`) override recommendations.
2. Structure signals (`R7`) constrain chunking before retrieval mode selection.
3. Exact-term vs semantic requirements (`R3`, `R5`) select retrieval candidates.
4. Objective and evidence breadth (`R2`, `R9`, `R10`) refine context and Top-K.
5. If two eligible profiles differ only weakly or signals conflict, return alternatives with low confidence rather than selecting a winner.

### Proposed confidence policy

- `high`: hard capability/safety boundary or repeated evidence with no material counterexample.
- `medium`: local and literature evidence align, but a target-specific benchmark is still recommended.
- `low`: heuristic starting point or small local difference; UI must require explicit confirmation before apply.
- `insufficient_data`: strategy cannot be justified from available corpus/provider signals; application is disabled.

## 9. Phase B boundary if approved

Router V1 may recommend and write only:

- Recursive vs current parent-child strategy and supported character parameters.
- Full-text, Vector or Hybrid mode and existing weights.
- Top-K and candidate multiplier within current bounds.
- Rerank only when a provider is ready, and only as an evaluation-required suggestion.

It must not:

- claim token-aware sizing;
- calibrate `score_threshold`;
- build, activate, promote or roll back an index;
- change Processor, visual settings or embedding model;
- implement semantic chunking, sentence windows, contextual chunks, Late Chunking or RAPTOR;
- convert low-confidence evidence into an automatic apply.

## 10. Review gate

Decision recorded on 2026-08-10: the four Rules V1 decisions below were reviewed
and accepted. Phase B is authorized to implement only the bounded deterministic
Router described in section 9. This approval does not authorize Auto Tuning,
provider calls, candidate index construction, threshold calibration, or activation.

Phase B should begin only if the user accepts these four decisions:

1. Short structured corpora and hash-based cross-language requirements may return `insufficient_data` instead of a forced recommendation.
2. Parent-child remains a low-confidence option for long-block/long-context workloads, not the default strategy.
3. Exact-term-heavy hash indexes may prefer Full-text; Hybrid with a real embedding remains a benchmark-required candidate, not a guaranteed improvement.
4. Router V1 keeps `score_threshold=0` and surfaces an abstention warning; threshold calibration is reserved for the next Auto Tuner round.

`RAG Strategy Rules V1` is therefore the fixed rule source for Router V1. Any
rule change must update its evidence classification, counterexample and local
experiment reference before production behavior changes.

### 10.1 Round 4A Router Rules V2 compatibility boundary

Round 4A changes the executable chunking contract without converting the
character-based Phase A evidence into token-budget evidence. Router Rules V2
therefore keeps only the estimated-token chunker already configured on the
current Pipeline Draft. It does not translate a V1 character size or overlap
into estimated tokens, does not propose a different token budget, and does not
restore the old character chunkers as buildable candidates.

Recommendations persist the rules version that produced them. A stored Rules V1
recommendation is `stale` under Router Rules V2 in both list and detail views and
cannot be applied. The corpus and current Draft must be analyzed again to create
a Rules V2 recommendation. This invalidation is intentional: preserving a V1
recommendation would allow character-derived heuristics to write into the new
estimated-token contract without supporting evidence.

The Auto Tuner exposes the same boundary explicitly: `chunker` is absent from
its `tunable` dimensions, appears under `deferred`, and reports
`chunker_search_status="frozen_until_calibrated_token_budget"`. Retrieval-mode,
Top-K, weight, threshold and bounded rerank experiments remain available under
their existing evidence gates. Chunker search can return only after a separate
calibrated estimated-token budget is approved; silently relabeling the old
character grid is not an acceptable substitute.

## 11. Benchmark Auto Tuner authorization and evidence boundary

Follow-up decision recorded on 2026-08-10: after Router V1 was accepted and
merged, the bounded Auto Tuner round was authorized. This does not revise the
Rules V1 evidence or turn any heuristic into a universal default. It adds a
target-specific empirical stage with these boundaries:

- fix one immutable V2 source snapshot and one published Evaluation Set Version;
- use the optimization split for threshold/candidate selection and reserve Holdout
  for finalist comparison;
- keep Processor, Vision and Embedding profiles unchanged;
- isolate and clean trial indexes, and never expose them as activatable versions;
- materialize only a candidate that passes the existing Evaluation Gate and an
  explicit quality, latency or index-size improvement threshold;
- rerun the full evaluation set on the materialized version;
- leave the result `promotion_required` for explicit human promotion.

The resulting ranking is evidence for that corpus, Gold distribution, provider
state and fixed run budget. It must not be cited as proof of a generally optimal
chunking or retrieval strategy.

## 12. Auto Tuner qualification audit and corrective boundary

The first Auto Tuner acceptance round did not produce a trustworthy promoted
candidate. Repeated runs exposed a system-level mismatch rather than one isolated
threshold bug:

- the synthetic catalog pack was designed as an engine regression guard but was
  used as winner-selection evidence;
- 34 answerable and 6 obvious out-of-domain no-result cases made the Holdout
  no-result gate too coarse and unstable;
- several nominally different chunk profiles produced the same realized chunk
  count and retrieval ranking, so the benchmark could not distinguish chunking;
- threshold selection optimized Recall first, while very small negative slices
  made abstention evidence effectively all-or-nothing;
- single-run latency was noisy enough to create false cost improvements.

The corrective implementation therefore starts with evidence qualification, not
with a wider parameter grid. Evaluation versions now carry one of four roles:
`unclassified`, `regression_guard`, `strategy_tuning`, or `promotion_evidence`.
Catalog regression packs remain runnable in the Evaluation workspace but cannot
start a formal tuning run or materialize a winner.

`RAG Strategy Tuning Readiness V1` requires at least 30 answerable cases before
retrieval selection. Threshold tuning additionally requires at least 12 reviewed,
corpus-near hard negatives. Cross-chunk tuning additionally requires stable
source-block Gold plus sparse, single-dense, and multi-dense evidence coverage.
Missing evidence disables only the affected dimension when safe; it is never
silently treated as passing evidence.

Chunk trials also record a content-free realized-index fingerprint and a ranked
result fingerprint under one fixed probe retrieval profile. If multiple nominal
profiles yield one realized outcome, non-baseline chunk candidates lose automatic
winner eligibility and the run continues as retrieval-only tuning. These hashes
contain IDs and counts, not document text or queries.

## 13. Auto Tuner 03B: threshold Pareto and semantic search space

03B corrects the two search-space defects isolated by the qualification audit.
Threshold selection now builds a non-dominated frontier over Recall@5, nDCG@10,
and hard-negative false-positive rate. A non-zero threshold is selected only when
it reduces false positives by at least `0.01` while keeping Recall and nDCG within
`0.02` of the zero-threshold profile. If no such point exists, the baseline
threshold is retained. Thresholds remain profile- and corpus-specific because
FTS confidence, vector similarity, and normalized RRF scores are not portable.

Retrieval candidates are now normalized by effective semantics. Full-text and
vector modes ignore inactive Hybrid weights; disabled Rerank ignores provider,
model, and top-N fields. After execution, candidates with the same realized-index,
ranking, and effective retrieval fingerprints cannot consume another winner slot.
The report exposes both nominal candidate count and unique semantic outcomes.

Targeted knowledge generation now has an explicit `strategy_tuning` purpose. It
can create 30-60 cases and defaults to 30 answerable cases plus 12 corpus-near
negative cases when threshold evidence is requested. Generated negatives remain
pending until a human reviews them and calibration is rerun; quantity alone never
confers tuning eligibility.

Real known-winner end-to-end fixtures remain intentionally separate. Until that
round passes, Auto Tuner output is corpus-specific comparative evidence rather
than proof of a generally reliable strategy optimizer.

## 14. Auto Tuner 03C: repeated Holdout evidence and robust latency

03C keeps the original optimization/Holdout boundary and does not expand the
search space. The fixed Holdout is evaluated with three queries per case. Each
case latency is reduced to its median before the run reports average and P95,
so a single cold start or scheduler spike cannot create a false latency win.
The optimization split still filters quality and execution errors, but its
single-query latency is diagnostic only and cannot reject a candidate before
the repeated Holdout measurement.

Finalist quality is compared to the baseline by case. Answerable cases use
nDCG@10 and approved no-result cases use no-result accuracy. Three deterministic
stratified bootstrap views are sampled only from the fixed Holdout; optimization
cases are never admitted. A deterministic 1,000-sample paired bootstrap then
reports a 90% confidence interval for the case-weighted quality delta.

The statistical gate is deliberately a non-degradation gate, not a universal
significance claim. Its lower confidence bound must remain within `-0.02`, and
at least two of the three stratified resamples must do the same. A candidate must
still pass the existing Promotion Gate and effective quality/cost improvement
rule. The baseline summary, validation plan checksum, per-case metric summaries,
and finalist intervals are persisted without queries or document text, allowing
restart recovery without repeating completed Holdout calls.

Small Holdouts still produce wide intervals; that is evidence uncertainty rather
than a software error. 03D must add real known-winner fixtures to prove that the
complete system can recover expected winners under controlled perturbations.

## 15. Auto Tuner 03D: known-winner system proof

03D adds a project-owned, versioned synthetic fixture at
`server/tests/fixtures/rag_strategy_tuner_known_winners.json`. It is not a product
benchmark and must not be used to claim universal retrieval quality. Its purpose
is narrower: prove that the complete tuner can recover a known intervention and
can abstain when the same intervention is already present.

The `threshold_recovery` scenario uses the real FTS5 index and ModelMirror lexical
confidence contract. Positive queries match every required policy term, while
approved corpus-near negatives share the policy identifier but contain absent
requirements. With the baseline threshold at zero, both groups are recalled.
The expected winner is therefore known before the run: a non-zero threshold above
the observed negative ceiling must preserve the stable source-block Gold, restore
no-result accuracy, pass repeated Holdout validation, materialize a normal ready
version, and leave the active version unchanged.

The `already_optimal_control` scenario rebuilds the same immutable corpus with
that safe threshold already fixed in the base version. A candidate whose chunker
and retrieval profile are exactly equal to the base is marked
`baseline_equivalent` before Holdout. It cannot become a winner because of timing
noise, and the run must finish `no_improvement` without creating a version.

Under the Round 4A content contract, the real 03D runs currently stop at lexical
admission: the fixture still requests the legacy lexical-v1 build path, while new
full-text indexes require lexical-v2. The two retained `wait_for_lexical_v2`
tests are the executable fail-closed evidence for that boundary. Round 4B must
restore the original end-to-end proof on lexical-v2 before any 03D result can be
claimed as runtime, Formal, materialization, or Promotion evidence.
`RagStrategyTuner.capabilities().validation` exposes this state as
`known_winner_validation_status="blocked_until_lexical_v2"`; the accompanying
scenario list is only the fixture catalog and must not be interpreted as current
known-winner validation evidence. Only the restored real lexical-v2 scenarios in
Round 4B may change that status back to a current/passing value.

Round 4A also keeps two explicitly named `synthetic_future` / `engine-only`
controls. They exercise generated-parent diversity plus threshold recovery, and
already-optimal plus semantic-duplicate abstention, using in-memory synthetic
rankings only. These controls prove the bounded algorithm behavior but do not
execute upload, parsing, FTS build, query, Holdout, Evaluation Gate,
materialization, or activation. The fixture and capability contract remain fixed
as `rag-strategy-known-winner-v1`; changing scoring or selection logic requires
the same controls and, after Round 4B, the restored full-text end-to-end scenarios
to pass without rewriting Gold to match the new behavior.

### Verified and unverified boundary

Verified by the current engine-only 03D controls:

- generated children sharing one stable parent cannot consume multiple diversity
  slots before threshold calibration;
- the synthetic score boundary recovers a safe non-zero threshold;
- an already-optimal baseline plus a semantically duplicate candidate abstains
  instead of manufacturing a winner.

Not yet proved by this fixture:

- the original real full-text end-to-end 03D path under lexical-v2, including
  Holdout, Evaluation Gate, materialization and `promotion_required` state;
- a known winner between Recursive and Parent-child chunking;
- Vector or Hybrid selection with a real semantic embedding provider;
- Rerank quality/cost selection;
- transfer of any selected threshold to another corpus or retrieval mode.

Those remain separate evidence tasks. Absence of those fixtures must be shown as
an unverified boundary, not inferred from the threshold proof.
