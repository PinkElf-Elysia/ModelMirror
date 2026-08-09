const PHASE_ORDER = Object.freeze({
  parse: 0,
  schema: 1,
  semantic: 2,
  integrity: 3,
});

const SORT_OFFSET = Symbol("diagnosticSortOffset");

function compareText(left, right) {
  if (left === right) {
    return 0;
  }
  return left < right ? -1 : 1;
}

function documentOrder(path) {
  if (path === "/runtimePack" || path.startsWith("/runtimePack/")) {
    return 0;
  }
  if (path === "/receipt" || path.startsWith("/receipt/")) {
    return 1;
  }
  return 2;
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
  path,
  relatedPath,
  location,
  sortOffset,
}) {
  const diagnostic = {
    phase,
    severity: "error",
    code,
    path,
    message: code,
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

function publishDiagnostic(diagnostic) {
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
    published.location = Object.freeze({
      line: diagnostic.location.line,
      column: diagnostic.location.column,
    });
  }
  return Object.freeze(published);
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

  return [...unique.values()]
    .sort((left, right) => {
      const phaseDifference = PHASE_ORDER[left.phase] - PHASE_ORDER[right.phase];
      if (phaseDifference !== 0) {
        return phaseDifference;
      }
      const documentDifference =
        documentOrder(left.path) - documentOrder(right.path);
      if (documentDifference !== 0) {
        return documentDifference;
      }
      const offsetDifference = left[SORT_OFFSET] - right[SORT_OFFSET];
      if (offsetDifference !== 0) {
        return offsetDifference;
      }
      return (
        compareText(left.path, right.path) ||
        compareText(left.code, right.code) ||
        compareText(left.relatedPath ?? "", right.relatedPath ?? "")
      );
    })
    .map(publishDiagnostic);
}

export function reportFromDiagnostics(diagnostics) {
  const stableDiagnostics = Object.freeze(sortDiagnostics(diagnostics));
  return Object.freeze({
    reportVersion: 1,
    valid: stableDiagnostics.length === 0,
    diagnostics: stableDiagnostics,
  });
}
