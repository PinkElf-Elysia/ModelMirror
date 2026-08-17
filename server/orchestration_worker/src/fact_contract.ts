export function extractClosedAuthoritativeFactSet(goal: string): string | null {
  const patterns = [
    /(?:可用|已知|已确认|确认的)?事实\s*(?:仅|只)\s*(?:包括|包含|有)\s*[：:]?\s*([^。\n]{2,800})/u,
    /(?:可用|已知|已确认|确认的)?事实\s*范围\s*(?:仅限于|只限于)\s*[：:]?\s*([^。\n]{2,800})/u,
    /(?:the\s+)?only\s+(?:available|known|confirmed)?\s*facts?\s+(?:include|are)\s*[:：]?\s*([^\n.]{2,800})/iu,
    /(?:available|known|confirmed)\s+facts?\s+only\s+(?:include|are)\s*[:：]?\s*([^\n.]{2,800})/iu,
    /(?:available|known|confirmed)\s+facts?\s+(?:include|are)\s+only\s*[:：]?\s*([^\n.]{2,800})/iu,
  ];
  for (const pattern of patterns) {
    const match = goal.match(pattern);
    const facts = match?.[1]?.replace(/\s+/g, ' ').trim();
    if (facts) return facts.slice(0, 800);
  }
  return null;
}

export function closedFactTaskContract(goal: string): string {
  const facts = extractClosedAuthoritativeFactSet(goal);
  if (!facts) return '';
  return `

ModelMirror closed authoritative fact set (system-extracted from the original request):
${facts}

This list is exhaustive for confirmed external facts at run start. Only values supplied by an explicitly resolved human_input step may extend it. Model-generated dependencies, drafts, and approval decisions do not establish additional external facts. Do not present any other factual or operational claim as confirmed, even when it is plausible. User-requested derived content such as analyses, options, objectives, questions, headings, or recommendations may be produced when its requested section visibly frames it as derived content and it does not invent a concrete external fact, resource, actor, date, number, system, or action. Preserve every user-requested structural element rather than deleting the structure or replacing it with TBD placeholders merely because its content must be derived. A bare fact list or a single sentence that collapses requested Q&A/sections is invalid. If the user requests N Q&A items and the closed facts can answer them, render N neutral question headings with N corresponding answers that directly restate those facts. When the requested Q&A count equals the number of listed closed facts, map them one-to-one: each fact must appear in exactly one Q&A, none may be moved outside the Q&A structure, and no fact may be repeated to make room for a new topic.`;
}

export function closedFactAcceptanceContract(goal: string): string {
  const facts = extractClosedAuthoritativeFactSet(goal);
  if (!facts) return '';
  return `
8. Closed authoritative fact set: ${facts}. Treat this set as exhaustive for confirmed external facts except for values supplied by a resolved human_input step. A model-generated or approved draft is not evidence for additional external facts. User-requested analyses, options, objectives, questions, headings, or recommendations may contain derived content when the requested section visibly frames it as derived and it introduces no unsupported concrete external fact, resource, actor, date, number, system, or action. Preserve all requested document structures and counts instead of replacing requested derived content with empty or TBD-only placeholders; a bare fact list or single collapsed sentence fails this criterion. For N requested Q&A items that can be answered directly by the closed facts, keep N question headings and N answers. When the requested Q&A count equals the number of listed closed facts, enforce a one-to-one mapping: each fact appears in exactly one Q&A, no fact sits outside the Q&A structure, and no fact is repeated to substitute for a new topic.`;
}

const QNA_COUNT_WORDS: Record<string, number> = {
  一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6,
};

function requestedQnaCount(goal: string): number | null {
  const match = goal.match(/([一二两三四五六1-6])\s*(?:个|组|条)?\s*(?:问答|Q\s*&\s*A)/iu);
  if (!match?.[1]) return null;
  const value = QNA_COUNT_WORDS[match[1]] ?? Number(match[1]);
  return Number.isInteger(value) && value >= 1 && value <= 6 ? value : null;
}

function neutralFactQuestion(fact: string, index: number): string {
  const normalized = fact.replace(/[。.]$/u, '').trim();
  const chinese = /[\u3400-\u9fff]/u.test(normalized);
  if (!chinese) return `Q${index}: What is confirmed fact ${index}?\nA${index}: ${normalized}.`;
  const assignment = normalized.match(/^(.{1,40}?)(?:为|是|[：:=])(.{1,120})$/u);
  const recipient = assignment ? null : normalized.match(/^(.{1,40}?给)(.{1,120})$/u);
  const label = assignment?.[1]?.trim() ?? recipient?.[1]?.trim() ?? '';
  let question = `第${index}项已知事实是什么？`;
  if (/适用对象|适用人群|受众|对象/u.test(label)) question = `${label}是谁？`;
  else if (/入口|地点|位置/u.test(label)) question = `${label}在哪里？`;
  else if (label.endsWith('给')) question = `${label}谁？`;
  else if (/责任人|负责人/u.test(label)) question = `${label}是谁？`;
  else if (label) question = `${label}是什么？`;
  return `Q${index}：${question}\nA${index}：${normalized}。`;
}

export function normalizeClosedFactQna(
  goal: string,
  output: string,
  markdown = false,
): string {
  const facts = extractClosedAuthoritativeFactSet(goal)
    ?.split(/[；;]/u)
    .map(item => item.trim())
    .filter(Boolean);
  const count = requestedQnaCount(goal);
  if (!facts || count === null || facts.length !== count) return output;
  const items = facts.map((fact, index) => neutralFactQuestion(fact, index + 1));
  if (!markdown) return items.join('\n\n');
  return `# FAQ\n\n${items.map(item => `## ${item}`).join('\n\n')}`;
}

export interface ClosedAuthoritativeList {
  label: string;
  values: string;
}

export function extractClosedAuthoritativeLists(value: string): ClosedAuthoritativeList[] {
  const matches = [...value.matchAll(
    /((?:可使用|允许使用|可用)?材料|(?:可使用|允许使用|可用)?资源|(?:可使用|允许使用|可用)?工具|适用对象|适用人群|受众|对象|字段|栏目)\s*(?:只有|仅包括|仅包含|仅限(?:于)?|只包括|只包含|只限(?:于)?)\s*([^。；;\n]{1,400})/gu,
  )];
  const seen = new Set<string>();
  return matches.flatMap(match => {
    const label = match[1]?.replace(/\s+/g, ' ').trim() ?? '';
    const values = match[2]?.replace(/\s+/g, ' ').trim() ?? '';
    const key = `${label}\u0000${values}`;
    if (!label || !values || seen.has(key)) return [];
    seen.add(key);
    return [{ label, values }];
  });
}

export function closedListTaskContract(value: string): string {
  const lists = extractClosedAuthoritativeLists(value);
  if (lists.length === 0) return '';
  return `

ModelMirror closed authoritative lists from resolved human_input:
${lists.map(item => `- ${item.label}: ${item.values}`).join('\n')}

Each list is exhaustive. Reproduce only its named entries. Do not add examples, subtypes, versions, accessories, alternatives, references, or extra TBD items.`;
}

export function closedListAcceptanceContract(value: string): string {
  const lists = extractClosedAuthoritativeLists(value);
  if (lists.length === 0) return '';
  return `
- Closed resolved-human-input lists: ${lists.map(item => `${item.label} = ${item.values}`).join('; ')}. Do not add examples, subtypes, versions, accessories, alternatives, references, or extra TBD items.`;
}
