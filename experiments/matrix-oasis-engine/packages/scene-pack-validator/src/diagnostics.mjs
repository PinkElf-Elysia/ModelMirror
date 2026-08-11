const PHASE_ORDER = Object.freeze({parse: 0, schema: 1, semantic: 2, integrity: 3});

export function pointer(base, token) { return `${base}/${String(token).replaceAll("~", "~0").replaceAll("/", "~1")}`; }
export function diagnostic(phase, code, path, relatedPath) {
  const value = {phase, severity: "error", code, path, message: code};
  if (relatedPath !== undefined) value.relatedPath = relatedPath;
  return value;
}
function compare(left, right) { return left === right ? 0 : left < right ? -1 : 1; }
export function report(diagnostics) {
  const unique = new Map();
  for (const item of diagnostics) {
    const key = [item.phase, item.code, item.path, item.relatedPath ?? ""].join("\0");
    if (!unique.has(key)) unique.set(key, item);
  }
  const published = [...unique.values()].sort((a, b) => PHASE_ORDER[a.phase] - PHASE_ORDER[b.phase] || compare(a.path, b.path) || compare(a.code, b.code)).map((item) => {
    const value = {phase: item.phase, severity: "error", code: item.code, path: item.path, message: item.code};
    if (item.relatedPath !== undefined) value.relatedPath = item.relatedPath;
    return Object.freeze(value);
  });
  return Object.freeze({reportVersion: 1, valid: published.length === 0, diagnostics: Object.freeze(published)});
}
