import type {
  LLMConfig,
  LLMConnector,
  LLMResult,
} from '../../vendor/agency-orchestrator/src/types.js';

import { ModelResponseRouter } from './channel.js';
import {
  AGENCY_EXECUTION_PROTOCOL,
  AGENCY_HITL_PROTOCOL,
  AgencyBridgeError,
  MAX_EXECUTION_MODEL_CALLS,
  MAX_EXECUTION_OUTPUT_BYTES,
  MAX_EXECUTION_OUTPUT_TOKENS,
  asObject,
} from './protocol.js';

const COMPACT_DELIVERABLE_CONTRACT = `

ModelMirror execution output contract:
- You are executing one DAG step. Produce only this step's deliverable; never simulate, answer, or reproduce other steps, human-input prompts, approval gates, or the overall workflow.
- Return a complete deliverable within 1,600 output tokens; do not try to consume the full token allowance.
- Prioritize required conclusions, evidence or labeled assumptions, concrete actions, and acceptance criteria.
- Omit repetition, generic background, and decorative exposition.
- Never invent human names, calendar dates, customer counts, budgets, performance targets, vendors, or infrastructure that are absent from the goal and dependency outputs.
- When required facts are missing, use role-based owners and explicit TBD/pending-confirmation placeholders. An acceptance criterion requesting a missing fact does not authorize fabrication.
- Numbers or concrete values that appear only in the current step instructions, including examples and suggested thresholds, are not user-provided evidence. Omit them or label each one inline as a proposal/example/TBD; a generic disclaimer is not enough.
- Explicit approval of a visible model-generated draft may approve its policy choices for finalization. It does not prove external facts in the draft, and the original request or resolved human input wins every conflict.
- When the authoritative request says a material, resource, field, or item list is "only", "仅", or "只有" the named entries, treat it as a closed set. Preserve those entries without adding examples, subtypes, templates, references, or extra TBD items.
- Aggregate capacity does not confirm individual availability, assignments, or that every person is simultaneously on duty. Keep those details as visibly labeled placeholders.
- A per-shift headcount is not additive across template rows and does not prove that every row runs every day. Without shift-specific availability, present rows as alternatives or templates and mark the operating schedule and cross-shift total as TBD.
- A per-session or per-event capacity does not establish sessions per day. Do not convert a per-session cap into a daily, weekly, or total volume unless the authoritative input supplies the session count; keep the aggregate as TBD.
- For reusable templates, leave unconfirmed times, owners, triggers, targets, and operating rules as labeled placeholders instead of filling plausible values.
- Finish the response cleanly; never trail off.`;

const COMPACT_DELIVERABLE_USER_REMINDER = `

Hard delivery constraints (treat these as acceptance requirements):
- Treat the current step instructions as the only requested deliverable. The overall goal and dependency outputs are context, not permission to execute later steps.
- Complete the deliverable in no more than 1,500 Chinese characters or 900 English words. Prefer compact tables and bullet points.
- Do not repeat the request or dependency outputs. Include only information needed to satisfy this step's acceptance criteria.
- Do not introduce names, vendors, dates, budgets, metrics, interfaces, or infrastructure absent from the supplied facts. Mark every necessary missing value as TBD/pending confirmation.
- Treat examples and suggested values in this step description as instructions, not confirmed facts. Any value absent from the original goal and dependency outputs must be omitted or labeled inline as proposed/example/TBD.
- Explicit draft approval can confirm policy decisions, not external facts. Preserve approved policy choices unless they conflict with the authoritative request or resolved human input.
- A list introduced as "only", "仅", or "只有" is closed: reproduce only the named materials, resources, fields, or items and do not expand it with examples, subtypes, templates, references, or TBD additions.
- A confirmed headcount or aggregate capacity does not establish named assignments, individual availability, or full-attendance claims.
- Do not sum per-shift staffing into a daily deployment or claim a staff pool can sustain every template row unless the user supplied shift-specific availability.
- Do not convert a per-session or per-event cap into a daily, weekly, or total volume unless the user supplied the session count; keep any aggregate as TBD.
- End with a complete sentence or table row; never continue until the model limit.`;

const JSON_REVIEW_EVIDENCE_CONTRACT = `

ModelMirror acceptance-review evidence rules:
- The failed array contains ONLY criteria that are actually unmet. It is not a full checklist: never include a satisfied criterion, especially when your own explanation says it is met.
- Judge each criterion literally. Do not add stronger requirements such as concrete calendar dates, extra approvals, or evidence that the criterion did not request.
- Judge semantic compliance, not lexical identity. A synonymous or more specific label satisfies a criterion unless the criterion explicitly requires verbatim wording or an exact term.
- Do not fail an explicit numeric cap based on speculation about future spending when the deliverable's stated total and cap are within the limit.
- A clear TBD/pending-confirmation marker satisfies a requirement to label missing information; do not fail merely because the missing value remains unknown.
- When a criterion asks that a subject or section be included but does not require an exact heading level or title, an explicitly labeled article, subsection, table section, or equivalent structure satisfies it. Do not impose document-convention preferences such as requiring a dedicated top-level chapter.
- A heading or document name is not itself an execution action. Do not treat a requested deliverable title such as a plan, FAQ, description, guide, checklist, or report as proof that an external action occurred.
- A descriptive title synthesized from the requested topic and audience is not a missing external fact merely because the user did not dictate its exact wording. Concrete dates, locations, headcounts, budgets, names, vendors, and systems still require authoritative support.
- Check staffing and capacity arithmetic across the entire deliverable. Fail a declared team or shift total that does not equal its listed role counts, and fail any daily cross-shift deployment inferred only from reusable per-shift template rows.
- Treat the authoritative original user request and any explicitly labeled resolved human_input block in the task as sources of confirmed facts. Model-generated dependency outputs are derived analysis.
- Explicit approval of a visible draft may approve its policy choices, but not external factual claims. For fact-boundary criteria, fail any draft claim that is presented as an external fact without authoritative support, and fail every approved policy that conflicts with the original request or resolved human input.
- Treat an authoritative list introduced as "only", "仅", or "只有" as a closed set. Fail every extra material, resource, field, or item, including additions labeled as examples, subtypes, templates, references, or TBD.
- When the task contains a "ModelMirror closed authoritative fact set", review every declarative factual or operational claim against that exact set. Plausibility, common practice, a dependency output, or an approved draft is not support. Fail each extra claim unless the precise value is supplied by a resolved human_input block or is explicitly marked inline as TBD/pending confirmation.
- A closed external-fact set does not prohibit user-requested derived analyses, options, objectives, questions, headings, or recommendations. Accept them when their requested section visibly frames them as derived content and they introduce no unsupported concrete external fact, resource, actor, date, number, system, or action. Do not require requested derived content to be replaced by empty or TBD-only placeholders.
- A closed fact set does not waive requested document structure. Neutral questions, headings, and direct restatements of the closed facts do not add external facts. Fail a deliverable that deletes a required question, answer, section, or item count instead of expressing it within the closed set.
- For every failed criterion, identify exact conflicting text or the exact required element that is absent after checking the entire deliverable, including headings and every table cell.
- Never claim that a literal label, value, fact, or section is absent when it appears in the relevant row, heading, or list item.
- A strict review must be evidence-based; do not fail a criterion on an unsupported assertion.`;

function deliverableFromReviewPrompt(userMessage: string): string {
  const markers = ['待验收产出：', 'Deliverable under review:'];
  const match = markers
    .map(marker => ({ marker, index: userMessage.lastIndexOf(marker) }))
    .filter(item => item.index >= 0)
    .sort((left, right) => right.index - left.index)[0];
  return match ? userMessage.slice(match.index + match.marker.length) : '';
}

function normalizedEvidence(value: string): string {
  return value.toLowerCase().replace(/[\s*_`#：:。.，,、()（）\[\]]+/g, '');
}

function phraseOccursOnlyInTitleLines(deliverable: string, phrase: string): boolean {
  const target = normalizedEvidence(phrase);
  if (!target) return false;
  const lines = deliverable.split(/\r?\n/u);
  const titleLines = new Set<number>();
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? '';
    if (/^\s*#\s+\S/u.test(line)) titleLines.add(index);
    if (/^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*标题\s*(?:\*\*|__)?\s*(?:[：:]|$)/u.test(line)) {
      titleLines.add(index);
      const next = lines.findIndex((candidate, candidateIndex) => candidateIndex > index && candidate.trim().length > 0);
      if (next > index) titleLines.add(next);
    }
  }
  const occurrences = lines
    .map((line, index) => normalizedEvidence(line).includes(target) ? index : -1)
    .filter(index => index >= 0);
  return occurrences.length > 0 && occurrences.every(index => titleLines.has(index));
}

function phraseOccursOnlyInDocumentTitle(deliverable: string, phrase: string): boolean {
  return /(?:说明(?:稿)?|简介|FAQ|方案|指南|清单|报告|文案|草案|手册|计划)/iu.test(phrase)
    && phraseOccursOnlyInTitleLines(deliverable, phrase);
}

function hasLabeledSection(deliverable: string, phrase: string): boolean {
  const target = normalizedEvidence(phrase);
  if (!target) return false;
  const lines = deliverable.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? '';
    const headingLike = /^\s*(?:#{1,6}\s+|\*\*|__|第.{1,12}[章节条]|[一二三四五六七八九十\d]+[.、）)])/u.test(line);
    if (!headingLike || !normalizedEvidence(line).includes(target)) continue;
    const body = lines.slice(index + 1, index + 5)
      .map(item => item.trim())
      .find(item => item.length > 0 && !/^[-*_#]+$/.test(item));
    if (
      body
      && (
        normalizedEvidence(body).length >= 8
        || (/^(?:[-*+]\s+|\d+[.、)]\s*)\S/u.test(body) && normalizedEvidence(body).length >= 2)
      )
    ) return true;
  }
  return false;
}

function hasExactNumberedSectionCount(deliverable: string, criterion: string, why: string): boolean {
  const numberValues = new Map([
    ['一', 1], ['二', 2], ['两', 2], ['三', 3], ['四', 4], ['五', 5],
    ['六', 6], ['七', 7], ['八', 8], ['九', 9], ['十', 10],
  ]);
  const expectedMatch = criterion.match(/([一二两三四五六七八九十]|\d+)\s*条\s*([^，。；;\n]{0,20}(?:规则|议程|问答|目标))/u);
  if (!expectedMatch) return false;
  const expected = /^\d+$/u.test(expectedMatch[1]) ? Number(expectedMatch[1]) : numberValues.get(expectedMatch[1]);
  const allegedMatch = why.match(/第\s*([一二两三四五六七八九十]|\d+)\s*条/u);
  const alleged = allegedMatch
    ? (/^\d+$/u.test(allegedMatch[1]) ? Number(allegedMatch[1]) : numberValues.get(allegedMatch[1]))
    : undefined;
  if (!expected || !alleged || alleged <= expected) return false;
  const target = normalizedEvidence(expectedMatch[2]);
  const lines = deliverable.split(/\r?\n/u);
  const headingIndex = lines.findIndex(line => (
    /^\s*#{1,6}\s+\S/u.test(line) && normalizedEvidence(line).includes(target)
  ));
  if (headingIndex < 0) return false;
  const level = lines[headingIndex].match(/^\s*(#{1,6})/u)?.[1].length ?? 6;
  const end = lines.findIndex((line, index) => (
    index > headingIndex
    && (line.match(/^\s*(#{1,6})\s+/u)?.[1].length ?? 7) <= level
  ));
  const section = lines.slice(headingIndex + 1, end > headingIndex ? end : lines.length);
  const numbered = section.filter(line => /^\s*\d+\s*[.、)]\s+\S/u.test(line));
  return numbered.length === expected;
}

function hasPendingMarkedTableColumn(deliverable: string, phrase: string): boolean {
  const target = normalizedEvidence(phrase);
  if (!target || /^(?:待确认|待补充|tbd|pending)$/iu.test(target)) return false;
  const lines = deliverable.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const header = lines[index] ?? '';
    if (!/^\s*\|/.test(header)) continue;
    const headerCells = header.split('|').slice(1, -1).map(cell => cell.trim());
    const column = headerCells.findIndex(cell => normalizedEvidence(cell).includes(target));
    if (column < 0 || !/^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$/.test(lines[index + 1] ?? '')) continue;
    const rows: string[][] = [];
    for (let rowIndex = index + 2; rowIndex < lines.length; rowIndex += 1) {
      const row = lines[rowIndex] ?? '';
      if (!/^\s*\|/.test(row)) break;
      rows.push(row.split('|').slice(1, -1).map(cell => cell.trim()));
    }
    if (rows.length > 0 && rows.every(row => (
      column < row.length
      && /(?:待确认|待补充|tbd|pending)/iu.test(row[column] ?? '')
    ))) return true;
  }
  return false;
}

function hasInlinePendingEvidence(deliverable: string, phrase: string): boolean {
  const target = normalizedEvidence(phrase);
  if (target.length < 4 || /^(?:待确认|待补充|tbd|pending)$/iu.test(target)) return false;
  return deliverable.split(/\r?\n/).some(line => (
    normalizedEvidence(line).includes(target)
    && /(?:待确认|待补充|tbd|pending)/iu.test(line)
  ));
}

function claimedTruncatedResponsibility(why: string): string {
  const zh = why.match(
    /(?:^|[，,。；;：:])\s*([^，,。；;：:()（）]{1,40}?)(?:的)?职责(?:描述)?(?:被截断|不完整|缺少|为空)/u,
  );
  if (zh?.[1]) return zh[1].trim();
  const en = why.match(
    /(?:^|[,.;:])\s*([^,.;:()]{1,40}?)\s+(?:responsibilities|duties)(?:\s+(?:are|were))?\s+(?:truncated|missing|incomplete|empty)/i,
  );
  return en?.[1]?.trim() ?? '';
}

function hasDetailedResponsibilityRow(deliverable: string, subject: string): boolean {
  const target = normalizedEvidence(subject);
  if (!target) return false;
  const lines = deliverable.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? '';
    if (!/^\s*\|/.test(line) || !normalizedEvidence(line).includes(target)) continue;
    const rows: string[] = [];
    for (let offset = 0; offset < 3; offset += 1) {
      const candidate = lines[index + offset] ?? '';
      if (!/^\s*\|/.test(candidate)) break;
      rows.push(candidate);
    }
    const numberedDuties = rows.join('\n').match(/(?:^|<br\s*\/?>)\s*\d{1,2}[.、)]\s*[^<|\n]+/gi) ?? [];
    if (numberedDuties.length >= 2) return true;
  }
  return false;
}

function hasRecordedFlowClosure(deliverable: string): boolean {
  const section = deliverable.match(
    /(?:^|\n)#{1,6}\s*[^\n]*(?:应急|emergency)[^\n]*\n([\s\S]*?)(?=\n#{1,6}\s|$)/i,
  )?.[1] ?? '';
  if (!section) return false;
  const citedStep = section.search(/(?:^|\n)\s*2[.、)]\s/u);
  if (citedStep < 0) return false;
  const later = section.slice(citedStep);
  return /(?:^|\n)\s*[3-9][.、)]\s*[^\n]*(?:记录|日志|record|log)/iu.test(later);
}

function hasCompleteInlineFlow(deliverable: string, phrase: string): boolean {
  const target = normalizedEvidence(phrase);
  if (!target || !hasRecordedFlowClosure(deliverable)) return false;
  return deliverable.split(/\r?\n/).some(line => {
    const normalized = normalizedEvidence(line);
    const index = normalized.indexOf(target);
    if (index < 0 || normalized.slice(index + target.length).length < 8) return false;
    return /[。！？；.!?;)）]\s*$/u.test(line.trim());
  });
}

function contradictsExplicitProhibitionEvidence(deliverable: string, why: string): boolean {
  const zh = why.match(/未明确(?:禁止|说明不得|说明不)\s*([^，。；]{2,40})/u);
  if (zh?.[1]) {
    const action = zh[1].trim();
    return [
      `不${action}`,
      `禁止${action}`,
      `不得${action}`,
    ].some(phrase => normalizedEvidence(deliverable).includes(normalizedEvidence(phrase)));
  }
  const en = why.match(/(?:does not|did not|fails? to) explicitly (?:prohibit|forbid|state (?:that )?it must not)\s+([^,.;]{2,80})/i);
  if (!en?.[1]) return false;
  const action = normalizedEvidence(en[1]);
  return [
    `do not ${action}`,
    `must not ${action}`,
    `prohibit ${action}`,
  ].some(phrase => normalizedEvidence(deliverable).includes(normalizedEvidence(phrase)));
}

function hasConcreteEmergencyTriggerAndManagerDuties(
  deliverable: string,
  criterion: string,
  why: string,
): boolean {
  const claimsCompleteEmergencySection = /(?:完整|complete).{0,20}(?:缺岗|应急|emergency)/iu.test(criterion);
  const claimsMissingTrigger = /(?:缺少|missing).{0,20}(?:触发条件|trigger condition)/iu.test(why);
  const claimsMissingManagerDuties = /(?:管理员|manager).{0,30}(?:职责|duties|responsibilit)/iu.test(why);
  if (!claimsCompleteEmergencySection || !claimsMissingTrigger || !claimsMissingManagerDuties) return false;
  const section = deliverable.match(
    /(?:^|\n)#{1,6}\s*[^\n]*(?:缺岗|应急|emergency)[^\n]*\n([\s\S]*?)(?=\n#{1,6}\s|$)/iu,
  )?.[1] ?? '';
  if (!section) return false;
  const hasTriggerTime = /班次开始前\s*\d{1,3}\s*分钟|\d{1,3}\s*minutes? before (?:the )?shift/iu.test(section);
  const hasTriggerCondition = /(?:若|当)[^\n]{0,40}缺岗[^\n]{0,20}(?:≥|>=|至少|1\s*人)[^\n]{0,30}(?:启动|应急)|if[^\n]{0,40}(?:absent|missing)[^\n]{0,30}(?:start|trigger|activate)/iu.test(section);
  const managerActions = ['清点', '顶岗', '记录', '复盘', '协调']
    .filter(action => new RegExp(`管理员[^\\n]{0,80}${action}|${action}[^\\n]{0,80}管理员`, 'u').test(section));
  return hasTriggerTime && hasTriggerCondition && managerActions.length >= 2;
}

function hasCompleteDocumentHeader(
  deliverable: string,
  criterion: string,
  why: string,
): boolean {
  const fieldMentions = [
    /(?:标题|名称|title|name)/iu.test(criterion),
    /(?:版本(?:号)?|version)/iu.test(criterion),
    /(?:生效日期|effective\s+date)/iu.test(criterion),
  ].filter(Boolean).length;
  if (!/(?:文件头|文档头|document\s+header)/iu.test(criterion) && fieldMentions < 2) return false;
  if (!/(?:标题|名称|版本|生效日期|title|name|version|effective\s+date|字段).{0,100}(?:缺少|未|missing|absent|直接缺失)|(?:缺少|未|missing|absent|直接缺失).{0,100}(?:标题|名称|版本|生效日期|title|name|version|effective\s+date|字段)/iu.test(why)) return false;
  const header = deliverable.split(/\r?\n/).slice(0, 24).join('\n');
  const hasTitle = /^\s*#{1,3}\s+\S.+$/mu.test(header)
    || /^\s*\*\*[^*\n]*(?:SOP|标准操作|standard operating)[^*\n]*\*\*/imu.test(header);
  const hasVersion = !/(?:版本|version)/iu.test(criterion) || /(?:版本(?:号)?|version)\s*[：:]?\s*\*{0,2}\s*[\w.-]+/iu.test(header);
  const hasEffectiveDate = !/(?:生效日期|effective\s+date)/iu.test(criterion)
    || /(?:生效日期|effective\s+date)\s*[：:]/iu.test(header);
  const hasRequiredBlankMarker = !/(?:留空|空白|blank|TBD)/iu.test(criterion)
    || /(?:生效日期|effective\s+date)\s*[：:]\*{0,2}\s*(?:_{2,}|—{2,}|-{2,}|【?待(?:填写|确认|定)|\[?TBD)/iu.test(header);
  return hasTitle && hasVersion && hasEffectiveDate && hasRequiredBlankMarker;
}

function hasAllLiteralDurationsWithoutRequestedAnchor(
  deliverable: string,
  criterion: string,
  why: string,
): boolean {
  if (/(?:起算|从何时|触发条件|开始计算|anchor|when\s+the\s+clock\s+starts)/iu.test(criterion)) return false;
  if (!/(?:未明确说明|描述不完整|没有说明).{0,120}(?:开始|起算|是指|还是)|does\s+not\s+clarify.{0,120}(?:start|whether)/iu.test(why)) return false;
  const durationPattern = /\d+(?:\.\d+)?\s*(?:个)?(?:工作日|自然日|天|年|月|周|小时|分钟|business\s+days?|calendar\s+days?|days?|years?|months?|weeks?|hours?|minutes?)/giu;
  const normalize = (value: string): string => value.toLowerCase().replace(/[\s个]+/gu, '');
  const required = [...criterion.matchAll(durationPattern)].map(match => normalize(match[0]));
  if (required.length === 0) return false;
  const present = new Set([...deliverable.matchAll(durationPattern)].map(match => normalize(match[0])));
  return required.every(value => present.has(value));
}

function hasRequiredDurationsAndOnlyLabeledAlternatives(
  deliverable: string,
  criterion: string,
  why: string,
): boolean {
  if (/(?:不得|禁止|不允许|仅可|no\s+other|must\s+not).{0,40}(?:时限|周期|duration|deadline)/iu.test(criterion)) return false;
  if (!/(?:不符|冲突|替换|改变|conflict|replace|change)/iu.test(why)) return false;
  const durationPattern = /\d+(?:\.\d+)?\s*(?:个)?(?:工作日|自然日|天|年|月|周|小时|分钟|business\s+days?|calendar\s+days?|days?|years?|months?|weeks?|hours?|minutes?)/giu;
  const normalize = (value: string): string => value.toLowerCase().replace(/[\s个]+/gu, '');
  const required = [...criterion.matchAll(durationPattern)].map(match => normalize(match[0]));
  if (required.length === 0) return false;
  const deliverableDurations = [...deliverable.matchAll(durationPattern)];
  const present = new Set(deliverableDurations.map(match => normalize(match[0])));
  if (!required.every(value => present.has(value))) return false;
  const allegedAlternatives = [...why.matchAll(durationPattern)]
    .map(match => normalize(match[0]))
    .filter(value => !required.includes(value));
  if (allegedAlternatives.length === 0) return false;
  const lines = deliverable.split(/\r?\n/);
  return allegedAlternatives.every(value => lines.some(line => (
    normalize(line).includes(value)
    && /(?:待确认|建议|示例|假设|proposal|tbd|pending|example)/iu.test(line)
  )));
}

function reviewerClaimsAllowedResourceUseIsOutOfScope(
  criterion: string,
  why: string,
): boolean {
  if (!/(?:超出|超过)[^。；;\n]{0,50}(?:资源|工具|系统|范围)|outside[^.\n]{0,50}(?:allowed|resource|tool|system|scope)/iu.test(why)) {
    return false;
  }
  const allowedMatch = criterion.match(/(?:仅使用|只使用|仅限使用|use only)\s*([^"'“”\n]{2,160}?)(?=["'“”]|的限制|，不|。|$)/iu);
  if (!allowedMatch?.[1]) return false;
  const normalize = (value: string): string => value.toLowerCase().replace(/(?:现有|existing)|[\s*_`#：:。.，,、()（）\[\]"'“”]/giu, '');
  const allowed = allowedMatch[1]
    .split(/、|和|及|\band\b/iu)
    .map(item => normalize(item.replace(/(?:两种|三种)?(?:现有)?(?:资源|工具|系统|介质)$/u, '')))
    .filter(item => item.length >= 2);
  if (allowed.length === 0) return false;
  const quotedOperations = [...why.matchAll(/[“"'‘]([^”"'’]{2,220})[”"'’]/gu)]
    .map(match => match[1] ?? '')
    .filter(value => value && !/(?:仅使用|只使用|仅限使用|use only)/iu.test(value));
  if (quotedOperations.length === 0) return false;
  return quotedOperations.every(operation => {
    const normalized = normalize(operation);
    return allowed.some(resource => normalized.includes(resource));
  });
}

export function reconcileReviewerEvidence(raw: string, userMessage: string): string {
  const deliverable = deliverableFromReviewPrompt(userMessage);
  if (!deliverable) return raw;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!Array.isArray(parsed.failed) || parsed.failed.length === 0) return raw;
    const remaining = parsed.failed.filter(item => {
      if (!item || typeof item !== 'object') return true;
      const criterion = String((item as Record<string, unknown>).criterion ?? '');
      const why = String((item as Record<string, unknown>).why ?? '');
      const explicitlySaysSatisfied = /(?:此项|该项|本项|这一项|criterion)[^。；;\n]{0,24}(?:已)?满足|(?:已|完全|明确)满足(?:该|此|本)?(?:项|标准|要求)|criterion\s+(?:is\s+)?satisfied/iu.test(why);
      const explicitlySaysUnmet = /(?:未|不|尚未|仅部分)[^。；;\n]{0,12}满足|(?:仅|只)?部分(?:满足|符合)|(?:not\s+met|unmet|partially\s+(?:met|satisfied))/iu.test(why);
      if (explicitlySaysSatisfied && !explicitlySaysUnmet) return false;
      const criterionRequiresExactWording = /逐字|原文|原词|确切(?:术语|措辞)|精确(?:术语|措辞)|verbatim|exact\s+(?:term|wording|phrase)/iu.test(criterion);
      const failureOnlyDemandsExactWording = /(?:替换|改写|换成|表述为)[^。；;\n]{0,120}(?:确切术语|原始术语|原词|原始措辞)|(?:different|equivalent|synonymous)\s+wording[^.\n]{0,100}(?:exact\s+(?:term|wording)|verbatim)/iu.test(why);
      if (failureOnlyDemandsExactWording && !criterionRequiresExactWording) return false;
      const criterionRequiresUnifiedList = /统一(?:列表|清单)|单一(?:列表|清单)|single\s+(?:list|checklist)|unified\s+(?:list|checklist)/iu.test(criterion);
      const failureOnlyDemandsUnifiedList = /(?:合计|共)\s*\d+\s*项[^。；\n]{0,80}(?:但|然而)[^。；\n]{0,80}(?:未以|没有|并非)[^。；\n]{0,30}统一(?:列表|清单)|(?:total|altogether)\s+\d+\s+items?[^.\n]{0,100}(?:split|separate)[^.\n]{0,40}(?:sub-?lists?|role\s+lists?)/iu.test(why);
      if (failureOnlyDemandsUnifiedList && !criterionRequiresUnifiedList) return false;
      if (reviewerClaimsAllowedResourceUseIsOutOfScope(criterion, why)) return false;
      const criterionRequiresStandalone = /独立(?:的)?[^。；;\n]{0,60}(?:章节|部分|条款)|固定(?:标题|层级)|standalone|dedicated|exact heading/iu.test(criterion);
      const failureClaimsMissingLabeledSection = /(?:缺少|没有|未包含|未提供)[^。；;\n]{0,100}(?:明确标识的)?[^。；;\n]{0,50}(?:部分|章节|栏目|清单)/u.test(why);
      const allegedMissingLabels = [...why.matchAll(/[“"'‘]([^”"'’]{2,80})[”"'’]/gu)]
        .map(match => match[1] ?? '')
        .filter(Boolean);
      if (
        failureClaimsMissingLabeledSection
        && !criterionRequiresStandalone
        && allegedMissingLabels.some(label => hasLabeledSection(deliverable, label))
      ) return false;
      const allegedExtraItem = why.match(
        /(?:新增|增加|包含|出现)(?:了)?[^。；;\n]{0,100}(?:项目|材料|物料)[“"'‘]([^”"'’]{1,80})[”"'’]/u,
      )?.[1]?.trim() ?? '';
      if (
        allegedExtraItem
        && /(?:材料|物料|清单)/u.test(`${criterion}\n${why}`)
        && !normalizedEvidence(deliverable).includes(normalizedEvidence(allegedExtraItem))
      ) return false;
      const allegedSplitItem = why.match(
        /(?:新增|增加|包含|出现)(?:了)?\s*[“"'‘]([^”"'’]{1,80})[”"'’][^。；;\n]{0,160}(?:子集|不同表述|完整[^。；;\n]{0,12}精确)/u,
      )?.[1]?.trim() ?? '';
      const combinedAuthoritativeList = why.match(
        /(?:用户提供|权威)[^。；;\n]{0,60}?(?:材料|清单)[^。；;\n]{0,24}?(?:中)?(?:只有|仅有|为)\s*[“"'‘]([^”"'’]{2,160})[”"'’]/u,
      )?.[1]?.trim() ?? '';
      const splitAuthoritativeItems = combinedAuthoritativeList
        .split(/[、，,]|(?:以及|或者|或|和|及)/u)
        .map(item => item.trim())
        .filter(item => item.length >= 2);
      if (
        allegedSplitItem
        && splitAuthoritativeItems.length >= 2
        && splitAuthoritativeItems.some(item => normalizedEvidence(item) === normalizedEvidence(allegedSplitItem))
        && splitAuthoritativeItems.every(item => normalizedEvidence(deliverable).includes(normalizedEvidence(item)))
      ) return false;
      const failureOnlyDemandsStandalone = /缺少独立的[^。；;\n]{1,80}(?:部分|章节)[^。；;\n]{0,160}(?:合并到|并入|merged into)[^。；;\n]{1,160}(?:独立(?:部分|章节)|standalone|dedicated)/iu.test(why);
      const mergedSectionQuotes = [...why.matchAll(/[“"'‘]([^”"'’]{2,120})[”"'’]/gu)]
        .map(match => match[1] ?? '')
        .filter(Boolean);
      if (
        failureOnlyDemandsStandalone
        && !criterionRequiresStandalone
        && mergedSectionQuotes.some(phrase => hasLabeledSection(deliverable, phrase))
      ) return false;
      const failureOnlyDemandsTitleSection = /缺少独立的[^。；;\n]{0,30}标题[^。；;\n]{0,30}(?:部分|章节)[^。；;\n]{0,120}(?:仅|已经|已)[^。；;\n]{0,40}(?:作为|用作)(?:文档)?标题/u.test(why)
        || /缺少独立的[^。；;\n]{0,30}标题[^。；;\n]{0,30}(?:部分|章节)[^。；;\n]{0,80}(?:当前|现有)[^。；;\n]{0,30}标题[^。；;\n]{0,80}(?:合并|混合|写入|置于)[^。；;\n]{0,40}(?:第一行|首行|一级标题|主标题)/u.test(why);
      const failureOnlyDemandsMainTitleLevel = /(?:标题被|将标题)[^。；;\n]{0,80}(?:二级|下级|子级)[^。；;\n]{0,80}(?:而非|不是|未作为)[^。；;\n]{0,60}(?:文档)?主标题/u.test(why);
      if (
        (failureOnlyDemandsTitleSection || failureOnlyDemandsMainTitleLevel)
        && !criterionRequiresStandalone
        && /^\s*#\s+\S.+$/mu.test(deliverable)
      ) return false;
      const criterionForbidsExecutionActions = /(?:不得|禁止|不允许|未新增|没有新增)[^。；;\n]{0,100}(?:执行动作|外部行动|外部操作)/u.test(criterion);
      const deliverableNameCalledAnAction = why.match(
        /(?:新增|出现)(?:了)?[“"']([^”"']{2,60})[”"'][^。；;\n]{0,40}(?:执行动作|外部行动|外部操作)[^。；;\n]{0,160}(?:本身|实际|其实)[^。；;\n]{0,30}(?:是|属于)[^。；;\n]{0,20}(?:交付物|文档|标题)/u,
      );
      if (
        criterionForbidsExecutionActions
        && deliverableNameCalledAnAction
        && deliverable.split(/\r?\n/u).some(line => (
          /^\s*#{1,6}\s+\S/u.test(line)
          && line.includes(deliverableNameCalledAnAction[1] ?? '')
        ))
      ) return false;
      const titlePhraseCalledAnAction = why.match(
        /(?:新增|出现)(?:了)?[“"'‘]([^”"'’]{2,80})[”"'’][^。；;\n]{0,80}(?:执行动作|外部行动|外部操作)/u,
      )?.[1] ?? '';
      if (
        criterionForbidsExecutionActions
        && titlePhraseCalledAnAction
        && phraseOccursOnlyInTitleLines(deliverable, titlePhraseCalledAnAction)
      ) return false;
      const criterionRequiresMissingFactsPending = /(?:缺失|未提供|未知|未确认)[^。；;\n]{0,100}(?:待确认|TBD|pending)/iu.test(criterion);
      const unconfirmedTitlePhrase = why.match(
        /标题[^。；;\n]{0,60}(?:新增|出现)(?:了)?[“"'‘]([^”"'’]{2,80})[”"'’][^。；;\n]{0,120}(?:缺失|未提供|未知|未确认)[^。；;\n]{0,60}(?:待确认|TBD|pending)/iu,
      )?.[1] ?? '';
      const citesConcreteExternalFact = /(?:日期|时间|地点|地址|人数|预算|金额|姓名|供应商|软件|系统|接口|目标值|calendar date|location|headcount|budget|name|vendor|system|interface)/iu.test(why);
      if (
        criterionRequiresMissingFactsPending
        && unconfirmedTitlePhrase
        && !citesConcreteExternalFact
        && phraseOccursOnlyInDocumentTitle(deliverable, unconfirmedTitlePhrase)
      ) return false;
      const genericMissingInfoCriterion = /(?:所有|全部|任何)[^。；;\n]{0,40}(?:缺失|未提供|未知|未确认)[^。；;\n]{0,60}(?:待确认|TBD|pending)/iu.test(criterion);
      const demandsOmittedExampleFields = /未对(?:任何|所有)?[^。；;\n]{0,30}(?:缺失|未提供|未知|未确认)[^。；;\n]{0,80}(?:如|例如)[^。；;\n]{0,120}(?:日期|地点|人数|预算|姓名|供应商|软件|系统|接口)[^。；;\n]{0,80}(?:待确认|TBD|pending)/iu.test(why);
      const deliverableHasBlankField = deliverable.split(/\r?\n/u).some(line => (
        /(?:日期|地点|人数|预算|姓名|供应商|软件|系统|接口)\s*[：:]/u.test(line)
        && !/(?:待确认|待补充|TBD|pending)/iu.test(line)
      ));
      if (genericMissingInfoCriterion && demandsOmittedExampleFields && !deliverableHasBlankField) {
        return false;
      }
      if (hasExactNumberedSectionCount(deliverable, criterion, why)) return false;
      const criterionOnlyRequiresUnknownNumbersPending = /(?:未提供|未知|未确定|缺失)[^。；\n]{0,50}(?:数值|目标值|数值目标)[^。；\n]{0,30}(?:待确认|TBD|pending)/iu.test(criterion);
      const failureReliesOnDerivedDraftMarker = /(?:原始|前序|上游|中间)[^。；\n]{0,30}(?:框架|草案|产出)[^。；\n]{0,60}(?:标注|标记|标为)[^。；\n]{0,20}(?:待确认|TBD|pending)/iu.test(why);
      const failureCitesConcreteNumber = /\d|百分比|%|金额|预算|样本量|时限|周期/iu.test(why);
      if (criterionOnlyRequiresUnknownNumbersPending && failureReliesOnDerivedDraftMarker && !failureCitesConcreteNumber) {
        return false;
      }
      if (hasCompleteDocumentHeader(deliverable, criterion, why)) return false;
      if (hasAllLiteralDurationsWithoutRequestedAnchor(deliverable, criterion, why)) return false;
      if (hasRequiredDurationsAndOnlyLabeledAlternatives(deliverable, criterion, why)) return false;
      if (criterionRequiresStandalone) {
        return true;
      }
      const quoted = [...why.matchAll(/[“‘"']([^”’"']{2,80})[”’"']/g)]
        .map(match => match[1] ?? '')
        .filter(Boolean);
      const failureRejectsPendingMarker = /(?:待确认|待补充|TBD|pending)[^。；;\n]{0,140}(?:未确认|非具体|不具体|不完整|not\s+confirmed|not\s+concrete|incomplete)/iu.test(why);
      const criterionExplicitlyForbidsPending = /(?:不得|禁止|不允许)[^。；;\n]{0,40}(?:待确认|待补充|TBD|pending)|(?:must\s+not|no)[^.\n]{0,30}(?:TBD|pending)|(?:YYYY|HH:MM|固定格式|确切格式|exact\s+format)/iu.test(criterion);
      if (
        failureRejectsPendingMarker
        && !criterionExplicitlyForbidsPending
        && quoted.some(phrase => hasInlinePendingEvidence(deliverable, phrase))
      ) return false;
      const claimsUnmarkedTableCells = /(?:表头|列名)[^。；\n]{0,140}(?:未|不|缺少)[^。；\n]{0,40}(?:单元格|标注|标记|待确认)|(?:column|header)[^.\n]{0,140}(?:cell|not\s+(?:marked|labeled))/iu.test(why);
      if (claimsUnmarkedTableCells && quoted.some(phrase => hasPendingMarkedTableColumn(deliverable, phrase))) {
        return false;
      }
      const claimsUnmarkedPendingValue = /(?:未|没有|并非)[^。；\n]{0,30}(?:标注|标记|标为)[^。；\n]{0,20}(?:待确认|待补充|tbd|pending)|not[^.\n]{0,30}(?:marked|labeled)[^.\n]{0,20}(?:tbd|pending)/iu.test(why);
      if (claimsUnmarkedPendingValue && quoted.some(phrase => hasInlinePendingEvidence(deliverable, phrase))) {
        return false;
      }
      if (!/(未.*(?:包含|出现|形成|列出|明确)|缺少|不存在|截断|不完整|absent|missing|truncated|incomplete|not (?:included|present|listed))/i.test(why)) {
        return true;
      }
      const claimsMissingLabeledElement = /(未.*(?:包含|出现|形成|列出)|缺少|不存在|absent|missing|not (?:included|present|listed))/i.test(why);
      if (claimsMissingLabeledElement && quoted.some(phrase => hasLabeledSection(deliverable, phrase))) {
        return false;
      }
      const responsibilitySubject = claimedTruncatedResponsibility(why);
      if (responsibilitySubject && hasDetailedResponsibilityRow(deliverable, responsibilitySubject)) {
        return false;
      }
      const claimsTruncatedResponsibilityRow = /(?:岗位分工|responsibilit|duties)[^\n]{0,100}(?:截断|truncated)/iu.test(why);
      const truncatedRole = why.match(/(?:在|at)\s*[“"']([^”"']{2,80})[”"']\s*(?:处)?(?:被截断|was truncated)/iu)?.[1] ?? '';
      if (
        claimsTruncatedResponsibilityRow
        && [...quoted, truncatedRole].filter(Boolean).some(phrase => hasDetailedResponsibilityRow(deliverable, phrase))
      ) {
        return false;
      }
      const claimsMissingFlowClosure = /(?:流程|flow).*(?:截断|truncated).*(?:结束|记录|closure|record)/iu.test(why);
      const criterionRequiresEveryBranch = /每(?:个|一).{0,10}(?:分支|规则).{0,10}(?:定义|闭环)|every.{0,20}branch/iu.test(criterion);
      if (claimsMissingFlowClosure && !criterionRequiresEveryBranch && hasRecordedFlowClosure(deliverable)) {
        return false;
      }
      const claimsTruncatedEmergencySection = /(?:缺岗|应急|emergency)[^\n]{0,100}(?:截断|truncated|内容不完整|incomplete)/iu.test(why);
      const namesSpecificTruncationPoint = /(?:在|at)\s*[“"'][^”"']{2,80}[”"']/iu.test(why);
      if (
        claimsTruncatedEmergencySection
        && !namesSpecificTruncationPoint
        && !criterionRequiresEveryBranch
        && hasRecordedFlowClosure(deliverable)
      ) {
        return false;
      }
      const claimsTruncatedInlineFlow = /(?:截断|truncated).*(?:不完整|incomplete)|(?:不完整|incomplete).*(?:截断|truncated)/iu.test(why);
      if (
        claimsTruncatedInlineFlow
        && !criterionRequiresEveryBranch
        && quoted.some(phrase => hasCompleteInlineFlow(deliverable, phrase))
      ) {
        return false;
      }
      const acknowledgesPendingEvidence = /(?:标注|标记|label(?:ed)?|mark(?:ed)?).{0,20}(?:待确认|待验证假设|tbd|pending|proposal)/iu.test(why);
      const demandsConfirmedEvidence = /(?:实测|验证通过|已验证|证明|证据|validated|proven|evidence|confirmed result)/iu.test(criterion);
      if (
        acknowledgesPendingEvidence
        && !demandsConfirmedEvidence
        && contradictsExplicitProhibitionEvidence(deliverable, why)
      ) {
        return false;
      }
      if (hasConcreteEmergencyTriggerAndManagerDuties(deliverable, criterion, why)) {
        return false;
      }
      return true;
    });
    if (remaining.length === parsed.failed.length) return raw;
    return JSON.stringify({
      ...parsed,
      pass: remaining.length === 0,
      failed: remaining,
    });
  } catch {
    return raw;
  }
}

export class ExecutionBridgeConnector implements LLMConnector {
  calls = 0;
  readonly usage = { input_tokens: 0, output_tokens: 0 };

  constructor(
    private readonly router: ModelResponseRouter,
    private readonly bridgeRequestId: string,
    private readonly modelId: string,
    private readonly protocol: typeof AGENCY_EXECUTION_PROTOCOL | typeof AGENCY_HITL_PROTOCOL = AGENCY_EXECUTION_PROTOCOL,
    initial?: {
      calls?: number;
      usage?: { input_tokens?: number; output_tokens?: number };
    },
  ) {
    this.calls = finiteUsage(initial?.calls);
    this.usage.input_tokens = finiteUsage(initial?.usage?.input_tokens);
    this.usage.output_tokens = finiteUsage(initial?.usage?.output_tokens);
  }

  async chat(systemPrompt: string, userMessage: string, config: LLMConfig): Promise<LLMResult> {
    const jsonResponse = Number(config.temperature ?? 0.3) === 0;
    const requestedMaxTokens = Math.max(1, Number(config.max_tokens ?? MAX_EXECUTION_OUTPUT_TOKENS));
    const maxTokens = jsonResponse
      ? Math.min(2000, Math.max(1600, requestedMaxTokens))
      : Math.min(MAX_EXECUTION_OUTPUT_TOKENS, requestedMaxTokens);
    const requestOnce = async (): Promise<LLMResult> => {
      if (this.calls >= MAX_EXECUTION_MODEL_CALLS) {
        throw new AgencyBridgeError(
          'agency_execution_budget_exceeded',
          `agency_execution_budget_exceeded: Agency execution exceeded ${MAX_EXECUTION_MODEL_CALLS} model calls.`,
        );
      }
      this.calls += 1;
      const callNumber = this.calls;
      const modelRequestId = `${this.bridgeRequestId}:model:${callNumber}`;
      const response = await this.router.request(modelRequestId, {
        protocol: this.protocol,
        type: 'model_request',
        id: this.bridgeRequestId,
        request_id: modelRequestId,
        call_number: callNumber,
        model_id: this.modelId,
        messages: [
          {
            role: 'system',
            content: `${systemPrompt}${COMPACT_DELIVERABLE_CONTRACT}${
              jsonResponse ? JSON_REVIEW_EVIDENCE_CONTRACT : ''
            }`,
          },
          {
            role: 'user',
            content: jsonResponse
              ? userMessage
              : `${userMessage}${COMPACT_DELIVERABLE_USER_REMINDER}`,
          },
        ],
        temperature: jsonResponse ? 0 : 0.3,
        max_tokens: maxTokens,
        timeout_seconds: 240,
        json_response: jsonResponse,
      });

      if (response.ok !== true) {
        const error = asObject(response.error ?? {}, 'model_response_invalid');
        const usage = asObject(error.usage ?? {}, 'model_response_invalid');
        this.usage.input_tokens += finiteUsage(usage.input_tokens);
        this.usage.output_tokens += finiteUsage(usage.output_tokens);
        const code = typeof error.code === 'string' ? error.code : 'model_call_failed';
        const message = typeof error.message === 'string' ? error.message : 'Model call failed.';
        throw new AgencyBridgeError(code, `${code}: ${message}`);
      }
      const result = asObject(response.result ?? {}, 'model_response_invalid');
      if (typeof result.content !== 'string' || !result.content.trim()) {
        throw new AgencyBridgeError('model_response_invalid', 'Model response content is empty.');
      }
      if (Buffer.byteLength(result.content, 'utf8') > MAX_EXECUTION_OUTPUT_BYTES) {
        throw new AgencyBridgeError(
          'agency_execution_budget_exceeded',
          'agency_execution_budget_exceeded: Model response exceeds the 64 KiB execution output limit.',
        );
      }
      const usage = asObject(result.usage ?? {}, 'model_response_invalid');
      const inputTokens = finiteUsage(usage.input_tokens);
      const outputTokens = finiteUsage(usage.output_tokens);
      this.usage.input_tokens += inputTokens;
      this.usage.output_tokens += outputTokens;
      if (result.finish_reason === 'length') {
        throw new AgencyBridgeError(
          'model_output_truncated',
          `model_output_truncated: Model output reached the ${MAX_EXECUTION_OUTPUT_TOKENS}-token limit. Retry only the failed steps after shortening their expected output.`,
        );
      }
      return {
        content: jsonResponse
          ? reconcileReviewerEvidence(result.content, userMessage)
          : result.content,
        usage: {
          input_tokens: inputTokens,
          output_tokens: outputTokens,
        },
      };
    };

    return await requestOnce();
  }
}

function finiteUsage(value: unknown): number {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(Number(value))) : 0;
}
