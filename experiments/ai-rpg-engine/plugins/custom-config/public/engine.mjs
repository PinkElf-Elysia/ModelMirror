// Called inside a disposable worker. Stop expensive expressions by terminating the worker.
import { validate } from "./config.mjs";
const join = (items) => items.filter(Boolean).join("\n\n");
function replace(source, rules, role, display) {
  for (const r of rules) {
    if (
      !r.enabled ||
      !r.pattern ||
      (r.scope !== "all" && r.scope !== role) ||
      (!display && r.renderOnly)
    )
      continue;
    source = boundedReplace(source, r);
  }
  return source;
}
import { boundedReplace } from "./replacement.mjs";
export function runJob({ kind, configs, messages, role, source, seed = 0 }) {
  if (!Array.isArray(configs) || configs.length > 2)
    throw Error("配置范围无效");
  configs.forEach(validate);
  const rules = configs.flatMap((c) => c.rules);
  if (kind === "display") return replace(source, rules, role, true);
  if (kind === "memory-source") {
    const data = JSON.parse(messages[1].content);
    const rows = data.turns ?? data.completedTurns ?? [data];
    for (const row of rows) {
      for (const role of ["user", "assistant"])
        if (typeof row[role] === "string")
          row[role] = replace(row[role], rules, role, false);
    }
    return [messages[0], { role: "user", content: JSON.stringify(data) }];
  }
  if (!configs.length) return messages;
  if (
    !Array.isArray(messages) ||
    messages.length < 2 ||
    messages[0].role !== "system" ||
    messages.at(-1).role !== "user"
  )
    throw Error("剧情消息结构无效");
  const c = configs.at(-1),
    depth = configs[0].depth;
  const sys = messages[0].content,
    input = replace(messages.at(-1).content, rules, "user", false);
  const prior = depth
    ? messages
        .slice(1, -1)
        .slice(-2 * depth)
        .map((m) => ({
          ...m,
          content: replace(m.content, rules, m.role, false),
        }))
    : [];
  const base = {
    system: configs.map((v) => v.system),
    prefix: configs.map((v) => v.prefix),
    suffix: configs.map((v) => v.suffix),
  };
  const book = configs
    .flatMap((v) => v.worldbook.entries)
    .filter((e) => e.enabled)
    .sort((a, b) => a.order - b.order);
  let work = 0;
  const selected = new Map();
  const random = () => {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  for (const phase of ["wrappers", "system"]) {
    const current =
      phase === "system"
        ? join([...base.prefix, input, ...base.suffix])
        : input;
    for (const e of book) {
      const targets = e.targets.filter((t) =>
        phase === "system" ? t === "system" : t !== "system",
      );
      if (!targets.length || !e.keywords.length) continue;
      const pool = [
        {
          role: "system",
          content: join([sys, ...configs.map((c) => c.system)]),
        },
        ...(e.depth ? prior.slice(-2 * e.depth) : []),
        { role: "user", content: current },
      ].filter((m) => e.roles.includes(m.role));
      work +=
        e.keywords.length * pool.reduce((n, m) => n + 1 + m.content.length, 0);
      if (work > 20000000) throw Error("世界书扫描超出容量");
      const hit = (k) => {
        const reg = e.regex ? new RegExp(k, "iu") : null;
        return pool.some((m) =>
          reg
            ? reg.test(m.content)
            : m.content.toLowerCase().includes(k.toLowerCase()),
        );
      };
      if (!(e.rule === "and" ? e.keywords.every(hit) : e.keywords.some(hit)))
        continue;
      if (!selected.has(e)) {
        let body = e.body;
        if (e.mode === "weighted") {
          const total = e.contents.reduce((n, c) => n + c.weight, 0);
          if (!total || !Number.isFinite(total))
            throw Error("世界书内容权重无效");
          let n = random() * total;
          body = e.contents.find((c) => (n -= c.weight) < 0)?.text ?? "";
        }
        selected.set(e, random() * 100 < e.chance ? body : "");
      }
      for (const t of targets) base[t].push(selected.get(e));
    }
  }
  const result = [
    { role: "system", content: join([sys, ...base.system]) },
    ...prior,
    { role: "user", content: join([...base.prefix, input, ...base.suffix]) },
  ];
  if (c.usePrefill && c.prefill)
    result.push({ role: "assistant", content: c.prefill });
  if (result.reduce((n, m) => n + m.content.length, 0) > 2000000)
    throw Error("配置后的消息过长");
  return result;
}
