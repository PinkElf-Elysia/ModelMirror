import { parseTree } from "jsonc-parser";
import {
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA,
  RUNTIME_GAME_PACK_SCHEMA,
} from "@matrix-oasis/runtime-pack-contracts";
import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const STRICT_PARSE_OPTIONS = Object.freeze({
  disallowComments: true,
  allowTrailingComma: false,
  allowEmptyContent: false,
});

const MAX_RAW_DOCUMENT_DEPTH = 256;

function exceedsRawDocumentDepth(text) {
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
      continue;
    }

    if (character === '"') {
      inString = true;
    } else if (character === "{" || character === "[") {
      depth += 1;
      if (depth > MAX_RAW_DOCUMENT_DEPTH) {
        return true;
      }
    } else if (character === "}" || character === "]") {
      depth -= 1;
    }
  }
  return false;
}

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

function decodePointerToken(value) {
  return value.replaceAll("~1", "/").replaceAll("~0", "~");
}

function resolveLocalSchemaReference(rootSchema, reference) {
  if (typeof reference !== "string" || !reference.startsWith("#/")) {
    return undefined;
  }
  let current = rootSchema;
  for (const token of reference.slice(2).split("/").map(decodePointerToken)) {
    if (
      !current ||
      typeof current !== "object" ||
      !Object.hasOwn(current, token)
    ) {
      return undefined;
    }
    current = current[token];
  }
  return current;
}

function expandSchemaCandidates(candidates, rootSchema) {
  const expanded = [];
  const pending = [...candidates];
  const visited = new Set();

  while (pending.length > 0) {
    const schema = pending.pop();
    if (!schema || typeof schema !== "object" || visited.has(schema)) {
      continue;
    }
    visited.add(schema);
    expanded.push(schema);

    const referenced = resolveLocalSchemaReference(rootSchema, schema.$ref);
    if (referenced) {
      pending.push(referenced);
    }
    for (const keyword of ["allOf", "anyOf", "oneOf"]) {
      if (Array.isArray(schema[keyword])) {
        pending.push(...schema[keyword]);
      }
    }
  }
  return expanded;
}

function propertySchemaCandidates(candidates, propertyName, rootSchema) {
  const childCandidates = [];
  for (const schema of expandSchemaCandidates(candidates, rootSchema)) {
    if (
      schema.properties &&
      typeof schema.properties === "object" &&
      Object.hasOwn(schema.properties, propertyName)
    ) {
      childCandidates.push(schema.properties[propertyName]);
    }
  }
  return childCandidates;
}

function itemSchemaCandidates(candidates, rootSchema) {
  const childCandidates = [];
  for (const schema of expandSchemaCandidates(candidates, rootSchema)) {
    if (schema.items && typeof schema.items === "object") {
      childCandidates.push(schema.items);
    }
  }
  return childCandidates;
}

function duplicateKeyDiagnostics(
  root,
  text,
  rootPath,
  code,
  rootSchema,
) {
  const diagnostics = [];
  const pending = [{
    node: root,
    pointer: rootPath,
    schemaCandidates: [rootSchema],
  }];

  while (pending.length > 0) {
    const { node, pointer, schemaCandidates } = pending.pop();
    if (node.type === "object") {
      const firstDeclarations = new Map();
      for (const property of node.children ?? []) {
        const [keyNode, valueNode] = property.children ?? [];
        if (!keyNode || !valueNode) {
          continue;
        }
        const childSchemaCandidates = propertySchemaCandidates(
          schemaCandidates,
          keyNode.value,
          rootSchema,
        );
        const childPointer = childSchemaCandidates.length > 0
          ? appendPointer(pointer, keyNode.value)
          : pointer;
        if (firstDeclarations.has(keyNode.value)) {
          diagnostics.push(
            makeDiagnostic({
              phase: "parse",
              code,
              path: childPointer,
              relatedPath: firstDeclarations.get(keyNode.value),
              location: locationAt(text, keyNode.offset),
              sortOffset: keyNode.offset,
            }),
          );
        } else {
          firstDeclarations.set(keyNode.value, childPointer);
        }
        pending.push({
          node: valueNode,
          pointer: childPointer,
          schemaCandidates: childSchemaCandidates,
        });
      }
    } else if (node.type === "array") {
      const childSchemaCandidates = itemSchemaCandidates(
        schemaCandidates,
        rootSchema,
      );
      for (const [index, child] of (node.children ?? []).entries()) {
        pending.push({
          node: child,
          pointer: childSchemaCandidates.length > 0
            ? appendPointer(pointer, index)
            : pointer,
          schemaCandidates: childSchemaCandidates,
        });
      }
    }
  }
  return diagnostics;
}

export function parseJsonDocument(text, descriptor) {
  if (typeof text !== "string") {
    return {
      ok: false,
      diagnostics: [
        makeDiagnostic({
          phase: "parse",
          code: descriptor.inputTypeCode,
          path: descriptor.rootPath,
        }),
      ],
    };
  }

  if (exceedsRawDocumentDepth(text)) {
    return {
      ok: false,
      diagnostics: [
        makeDiagnostic({
          phase: "parse",
          code: descriptor.depthExceededCode,
          path: descriptor.rootPath,
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
          code: descriptor.syntaxCode,
          path: descriptor.rootPath,
          location: locationAt(text, firstError.offset),
          sortOffset: firstError.offset,
        }),
      ],
    };
  }

  const duplicateDiagnostics = duplicateKeyDiagnostics(
    root,
    text,
    descriptor.rootPath,
    descriptor.duplicateKeyCode,
    descriptor.schema,
  );
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
          code: descriptor.syntaxCode,
          path: descriptor.rootPath,
          location: { line: 1, column: 1 },
          sortOffset: 0,
        }),
      ],
    };
  }
}

export const RUNTIME_PACK_DOCUMENT = Object.freeze({
  rootPath: "/runtimePack",
  inputTypeCode: "RUNTIME_PACK_JSON_INPUT_TYPE",
  syntaxCode: "RUNTIME_PACK_JSON_SYNTAX",
  duplicateKeyCode: "RUNTIME_PACK_JSON_DUPLICATE_KEY",
  depthExceededCode: "RUNTIME_PACK_JSON_DEPTH_EXCEEDED",
  schema: RUNTIME_GAME_PACK_SCHEMA,
});

export const RECEIPT_DOCUMENT = Object.freeze({
  rootPath: "/receipt",
  inputTypeCode: "RUNTIME_RECEIPT_JSON_INPUT_TYPE",
  syntaxCode: "RUNTIME_RECEIPT_JSON_SYNTAX",
  duplicateKeyCode: "RUNTIME_RECEIPT_JSON_DUPLICATE_KEY",
  depthExceededCode: "RUNTIME_RECEIPT_JSON_DEPTH_EXCEEDED",
  schema: RUNTIME_GAME_PACK_RECEIPT_SCHEMA,
});
