const PHASE_ORDER = Object.freeze({ parse: 0, schema: 1, semantic: 2 });
const SORT_OFFSET = Symbol("diagnosticSortOffset");

function compareText(left, right) {
  if (left === right) {
    return 0;
  }
  return left < right ? -1 : 1;
}

export function escapePointerToken(value) {
  return String(value).replaceAll("~", "~0").replaceAll("/", "~1");
}

export function appendPointer(base, value) {
  return `${base}/${escapePointerToken(value)}`;
}

export function makeDiagnostic({
  phase,
  code,
  path = "",
  message,
  relatedPath,
  location,
  sortOffset,
}) {
  const diagnostic = {
    phase,
    severity: "error",
    code,
    path,
    message,
  };
  if (relatedPath !== undefined) {
    diagnostic.relatedPath = relatedPath;
  }
  if (location !== undefined) {
    diagnostic.location = {
      line: location.line,
      column: location.column,
    };
  }
  Object.defineProperty(diagnostic, SORT_OFFSET, {
    value: Number.isSafeInteger(sortOffset) ? sortOffset : Number.MAX_SAFE_INTEGER,
    enumerable: false,
  });
  return diagnostic;
}

export function sortDiagnostics(diagnostics) {
  const unique = new Map();
  for (const diagnostic of diagnostics) {
    const locationKey = diagnostic.location
      ? `${diagnostic.location.line}:${diagnostic.location.column}`
      : "";
    const key = [
      diagnostic.phase,
      diagnostic.code,
      diagnostic.path,
      diagnostic.relatedPath ?? "",
      locationKey,
    ].join("\0");
    if (!unique.has(key)) {
      unique.set(key, diagnostic);
    }
  }

  const sorted = [...unique.values()].sort((left, right) => {
    const phaseDifference = PHASE_ORDER[left.phase] - PHASE_ORDER[right.phase];
    if (phaseDifference !== 0) {
      return phaseDifference;
    }
    const offsetDifference = left[SORT_OFFSET] - right[SORT_OFFSET];
    if (offsetDifference !== 0) {
      return offsetDifference;
    }
    return (
      compareText(left.path, right.path) ||
      compareText(left.code, right.code) ||
      compareText(left.relatedPath ?? "", right.relatedPath ?? "") ||
      compareText(left.message, right.message)
    );
  });

  return sorted.map((diagnostic) => {
    const published = {
      phase: diagnostic.phase,
      severity: diagnostic.severity,
      code: diagnostic.code,
      path: diagnostic.path,
      message: diagnostic.message,
    };
    if (diagnostic.relatedPath !== undefined) {
      published.relatedPath = diagnostic.relatedPath;
    }
    if (diagnostic.location !== undefined) {
      published.location = {
        line: diagnostic.location.line,
        column: diagnostic.location.column,
      };
    }
    return published;
  });
}

export function reportFromDiagnostics(diagnostics) {
  const stableDiagnostics = sortDiagnostics(diagnostics);
  return {
    reportVersion: 1,
    valid: stableDiagnostics.length === 0,
    diagnostics: stableDiagnostics,
  };
}
