export function diagnostic(phase, code, path = "") {
  return Object.freeze({ phase, severity: "error", code, path, message: code });
}

export function failure(code, phase = "input", path = "") {
  return Object.freeze({
    ok: false,
    diagnostics: Object.freeze([diagnostic(phase, code, path)]),
  });
}

export function report(diagnostics) {
  const frozen = Object.freeze([...diagnostics]);
  return Object.freeze({ reportVersion: 1, valid: frozen.length === 0, diagnostics: frozen });
}
