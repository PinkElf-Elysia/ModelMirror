const encoder = new TextEncoder();
const DISPLAY_REGEX_LIMITS = { sourceBytes: 2 * 1024 * 1024, matches: 100000 };
const issue = (code, id) => Error(code + " (" + id + ")");
// ECMAScript replacement tokens are expanded as data. No replacement functions
// or generated code are accepted. Append limits are checked before allocating
// the complete result, including empty matches and prefix/suffix substitutions.
function substitute(template, match, source, append) {
  let cursor = 0;
  for (let i = 0; i < template.length; i++) {
    if (template[i] !== "$" || i + 1 === template.length) continue;
    const next = template[i + 1];
    let value,
      end = i + 2;
    if (next === "$") value = "$";
    else if (next === "&") value = match[0];
    else if (next === "`") value = source.slice(0, match.index);
    else if (next === "'") value = source.slice(match.index + match[0].length);
    else if (next === "<" && match.groups !== undefined) {
      const close = template.indexOf(">", i + 2);
      if (close >= 0) {
        value = Object.hasOwn(match.groups, template.slice(i + 2, close))
          ? (match.groups[template.slice(i + 2, close)] ?? "")
          : "";
        end = close + 1;
      }
    } else if (/[0-9]/.test(next)) {
      let n = Number(next);
      const second = template[i + 2];
      if (
        second !== undefined &&
        /[0-9]/.test(second) &&
        Number(next + second) > 0 &&
        Number(next + second) < match.length
      ) {
        n = Number(next + second);
        end++;
      }
      if (n > 0 && n < match.length) value = match[n] ?? "";
    }
    if (value === undefined) continue;
    append(template.slice(cursor, i));
    append(value);
    i = end - 1;
    cursor = end;
  }
  append(template.slice(cursor));
}
export function boundedReplace(source, rule) {
  if (rule.pattern === "") throw issue("DISPLAY_REGEX_EMPTY", rule.id);
  let expression;
  try {
    expression = new RegExp(rule.pattern, rule.flags);
  } catch {
    throw issue("DISPLAY_REGEX_SYNTAX", rule.id);
  }
  const chunks = [];
  let bytes = 0,
    cursor = 0,
    matches = 0,
    match;
  const append = (value) => {
    bytes += encoder.encode(value).byteLength;
    if (bytes > DISPLAY_REGEX_LIMITS.sourceBytes)
      throw issue("DISPLAY_REGEX_CAPACITY", rule.id);
    if (value) chunks.push(value);
  };
  while ((match = expression.exec(source)) !== null) {
    if (++matches > DISPLAY_REGEX_LIMITS.matches)
      throw issue("DISPLAY_REGEX_MATCH_LIMIT", rule.id);
    append(source.slice(cursor, match.index));
    substitute(rule.replacement, match, source, append);
    cursor = match.index + match[0].length;
    if (!expression.global) break;
    if (match[0] === "") {
      const p = expression.lastIndex;
      expression.lastIndex =
        p + (expression.unicode && source.codePointAt(p) > 0xffff ? 2 : 1);
    }
  }
  append(source.slice(cursor));
  return chunks.join("");
}
