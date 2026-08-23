import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_CREATOR_QUALIFICATION_LIMITS,
  PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA,
} from "./schema.mjs";

const INTERNAL_CODE = "PROTOTYPE_CREATOR_QUALIFICATION_CONTRACT_INTERNAL_ERROR";
const DIAGNOSTIC_PREFIX = "PROTOTYPE_CREATOR_QUALIFICATION";

const ajv = new Ajv2020({
  strict: true,
  allErrors: true,
  ownProperties: true,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  validateFormats: false,
});
const validateSchema = ajv.compile(PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA);

export class PrototypeCreatorQualificationContractOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "PrototypeCreatorQualificationContractOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    deepFreeze(child);
  }
  return Object.freeze(value);
}

function diagnostic(phase, code, path = "") {
  return { phase, severity: "error", code, path, message: code };
}

function report(diagnostics) {
  const sorted = [...diagnostics].sort(
    (left, right) =>
      left.path.localeCompare(right.path) || left.code.localeCompare(right.code),
  );
  const output = [];
  const seen = new Set();
  for (const item of sorted) {
    const key = `${item.path}\0${item.code}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    output.push(deepFreeze({ ...item }));
  }
  return deepFreeze({
    reportVersion: 1,
    valid: output.length === 0,
    diagnostics: output,
  });
}

function escapePointer(value) {
  return value.replaceAll("~", "~0").replaceAll("/", "~1");
}

function parseStrict(text) {
  if (typeof text !== "string") {
    return {
      diagnostics: [diagnostic("parse", `${DIAGNOSTIC_PREFIX}_JSON_INPUT_TYPE`)],
    };
  }
  if (
    new TextEncoder().encode(text).byteLength >
    PROTOTYPE_CREATOR_QUALIFICATION_LIMITS.documentBytes
  ) {
    return {
      diagnostics: [
        diagnostic("parse", `${DIAGNOSTIC_PREFIX}_JSON_SIZE_EXCEEDED`),
      ],
    };
  }

  let depth = 0;
  let inString = false;
  let escaped = false;
  for (const character of text) {
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
    } else if (character === '"') {
      inString = true;
    } else if (character === "{" || character === "[") {
      depth += 1;
      if (depth > PROTOTYPE_CREATOR_QUALIFICATION_LIMITS.documentDepth) {
        return {
          diagnostics: [
            diagnostic("parse", `${DIAGNOSTIC_PREFIX}_JSON_DEPTH_EXCEEDED`),
          ],
        };
      }
    } else if (character === "}" || character === "]") {
      depth -= 1;
    }
  }

  const errors = [];
  const value = parse(text, errors, {
    allowTrailingComma: false,
    disallowComments: true,
    allowEmptyContent: false,
  });
  if (errors.length > 0 || value === undefined) {
    return {
      diagnostics: [diagnostic("parse", `${DIAGNOSTIC_PREFIX}_JSON_SYNTAX`)],
    };
  }

  const tree = parseTree(text, [], {
    allowTrailingComma: false,
    disallowComments: true,
    allowEmptyContent: false,
  });
  const stack = tree ? [{ node: tree, path: "" }] : [];
  const duplicates = [];
  while (stack.length > 0) {
    const { node, path } = stack.pop();
    if (node.type === "object") {
      const keys = new Set();
      for (const property of node.children ?? []) {
        const key = property.children?.[0]?.value;
        const child = property.children?.[1];
        if (typeof key !== "string" || !child) {
          continue;
        }
        if (keys.has(key)) {
          duplicates.push(
            diagnostic("parse", `${DIAGNOSTIC_PREFIX}_JSON_DUPLICATE_KEY`, path),
          );
        }
        keys.add(key);
        stack.push({ node: child, path: `${path}/${escapePointer(key)}` });
      }
    } else if (node.type === "array") {
      for (const [index, child] of (node.children ?? []).entries()) {
        stack.push({ node: child, path: `${path}/${index}` });
      }
    }
  }
  return duplicates.length > 0 ? { diagnostics: duplicates } : { value };
}

function containsOnlyWellFormedText(value) {
  const stack = [value];
  while (stack.length > 0) {
    const current = stack.pop();
    if (typeof current === "string") {
      for (let index = 0; index < current.length; index += 1) {
        const unit = current.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          const next = current.charCodeAt(index + 1);
          if (!(next >= 0xdc00 && next <= 0xdfff)) {
            return false;
          }
          index += 1;
        } else if (unit >= 0xdc00 && unit <= 0xdfff) {
          return false;
        }
      }
    } else if (Array.isArray(current)) {
      stack.push(...current);
    } else if (current && typeof current === "object") {
      stack.push(...Object.keys(current), ...Object.values(current));
    }
  }
  return true;
}

function schemaDiagnostics() {
  const suffix = {
    required: "REQUIRED",
    additionalProperties: "UNKNOWN_PROPERTY",
    type: "TYPE",
    const: "CONST",
    enum: "ENUM",
    minimum: "NUMBER_CONSTRAINT",
    maximum: "NUMBER_CONSTRAINT",
    minLength: "STRING_CONSTRAINT",
    maxLength: "STRING_CONSTRAINT",
    pattern: "STRING_CONSTRAINT",
  };
  return (validateSchema.errors ?? []).map((error) =>
    diagnostic(
      "schema",
      `${DIAGNOSTIC_PREFIX}_SCHEMA_${suffix[error.keyword] ?? "INVALID"}`,
      error.keyword === "required"
        ? `${error.instancePath}/${error.params.missingProperty}`
        : error.instancePath,
    ),
  );
}

function semanticDiagnostics(value) {
  const output = [];
  if (`sha256:${value.evidence.runId}` !== value.hashes.runtimeEvidenceSha256) {
    output.push(
      diagnostic(
        "semantic",
        `${DIAGNOSTIC_PREFIX}_EVIDENCE_RUN_ID_MISMATCH`,
        "/evidence/runId",
      ),
    );
  }
  if (value.evidence.screenshotCount < value.evidence.replayCount) {
    output.push(
      diagnostic(
        "semantic",
        `${DIAGNOSTIC_PREFIX}_SCREENSHOT_COVERAGE_INCOMPLETE`,
        "/evidence/screenshotCount",
      ),
    );
  }
  return output;
}

export function validatePrototypeCreatorQualificationJson(text) {
  try {
    const parsed = parseStrict(text);
    if (parsed.diagnostics) {
      return report(parsed.diagnostics);
    }
    if (!containsOnlyWellFormedText(parsed.value)) {
      return report([
        diagnostic(
          "semantic",
          `${DIAGNOSTIC_PREFIX}_TEXT_UNPAIRED_SURROGATE`,
        ),
      ]);
    }
    if (!validateSchema(parsed.value)) {
      return report(schemaDiagnostics());
    }
    const semantics = semanticDiagnostics(parsed.value);
    if (semantics.length > 0) {
      return report(semantics);
    }
    if (canonicalizeJsonValue(parsed.value) !== text) {
      return report([
        diagnostic("integrity", `${DIAGNOSTIC_PREFIX}_JSON_NON_CANONICAL`),
      ]);
    }
    return report([]);
  } catch (error) {
    if (error instanceof PrototypeCreatorQualificationContractOperationalError) {
      throw error;
    }
    throw new PrototypeCreatorQualificationContractOperationalError();
  }
}
