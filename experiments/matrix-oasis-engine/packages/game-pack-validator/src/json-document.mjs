import { parseTree } from "jsonc-parser";
import { AUTHORING_GAME_PACK_SCHEMA } from "@matrix-oasis/game-pack-contracts";
import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const STRICT_PARSE_OPTIONS = Object.freeze({
  disallowComments: true,
  allowTrailingComma: false,
  allowEmptyContent: false,
});

function collectSchemaPropertyNames(value, names = new Set()) {
  if (!value || typeof value !== "object") {
    return names;
  }
  if (value.properties && typeof value.properties === "object") {
    for (const propertyName of Object.keys(value.properties)) {
      names.add(propertyName);
    }
  }
  for (const child of Object.values(value)) {
    collectSchemaPropertyNames(child, names);
  }
  return names;
}

const SAFE_PROPERTY_NAMES = collectSchemaPropertyNames(
  AUTHORING_GAME_PACK_SCHEMA,
);

function locationAt(text, requestedOffset) {
  const offset = Math.max(0, Math.min(requestedOffset, text.length));
  let line = 1;
  let lineStart = 0;
  for (let index = 0; index < offset; index += 1) {
    if (text[index] === "\r") {
      if (text[index + 1] === "\n") {
        index += 1;
      }
      line += 1;
      lineStart = index + 1;
    } else if (text[index] === "\n") {
      line += 1;
      lineStart = index + 1;
    }
  }
  return { line, column: offset - lineStart + 1 };
}

function duplicateKeyDiagnostics(root, text) {
  const diagnostics = [];

  function visit(node, pointer) {
    if (node.type === "object") {
      const firstDeclarations = new Map();
      for (const property of node.children ?? []) {
        const [keyNode, valueNode] = property.children ?? [];
        if (!keyNode || !valueNode) {
          continue;
        }
        const safePropertyName = SAFE_PROPERTY_NAMES.has(keyNode.value);
        const childPointer = safePropertyName
          ? appendPointer(pointer, keyNode.value)
          : pointer;
        if (firstDeclarations.has(keyNode.value)) {
          diagnostics.push(
            makeDiagnostic({
              phase: "parse",
              code: "PACK_JSON_DUPLICATE_KEY",
              path: childPointer,
              message: "Object property is duplicated.",
              relatedPath: firstDeclarations.get(keyNode.value),
              location: locationAt(text, keyNode.offset),
              sortOffset: keyNode.offset,
            }),
          );
        } else {
          firstDeclarations.set(keyNode.value, childPointer);
        }
        visit(valueNode, childPointer);
      }
      return;
    }
    if (node.type === "array") {
      for (const [index, child] of (node.children ?? []).entries()) {
        visit(child, appendPointer(pointer, index));
      }
    }
  }

  visit(root, "");
  return diagnostics;
}

export function parseAuthoringGamePackJson(text) {
  if (typeof text !== "string") {
    return {
      ok: false,
      diagnostics: [
        makeDiagnostic({
          phase: "parse",
          code: "PACK_JSON_INPUT_TYPE",
          path: "",
          message: "JSON input must be a string.",
        }),
      ],
    };
  }

  const parseErrors = [];
  const root = parseTree(text, parseErrors, STRICT_PARSE_OPTIONS);
  if (parseErrors.length > 0 || !root) {
    const firstError = [...parseErrors].sort(
      (left, right) => left.offset - right.offset || left.error - right.error,
    )[0] ?? { offset: 0 };
    return {
      ok: false,
      diagnostics: [
        makeDiagnostic({
          phase: "parse",
          code: "PACK_JSON_SYNTAX",
          path: "",
          message: "Document is not strict JSON.",
          location: locationAt(text, firstError.offset),
          sortOffset: firstError.offset,
        }),
      ],
    };
  }

  const duplicateDiagnostics = duplicateKeyDiagnostics(root, text);
  if (duplicateDiagnostics.length > 0) {
    return { ok: false, diagnostics: duplicateDiagnostics };
  }

  try {
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return {
      ok: false,
      diagnostics: [
        makeDiagnostic({
          phase: "parse",
          code: "PACK_JSON_SYNTAX",
          path: "",
          message: "Document is not strict JSON.",
          location: { line: 1, column: 1 },
          sortOffset: 0,
        }),
      ],
    };
  }
}
