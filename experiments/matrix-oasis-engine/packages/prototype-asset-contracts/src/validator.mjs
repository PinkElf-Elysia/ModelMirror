import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_ASSET_BUNDLE_SCHEMA,
  PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE,
  PROTOTYPE_ASSET_LIMITS,
  PROTOTYPE_ASSET_NORMALIZATION_PROFILE,
} from "./schema.mjs";

const PHASE_ORDER = Object.freeze({
  parse: 0,
  schema: 1,
  semantic: 2,
  integrity: 3,
});

export class PrototypeAssetContractOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_ASSET_CONTRACT_INTERNAL_ERROR");
    this.name = "PrototypeAssetContractOperationalError";
    this.code = "PROTOTYPE_ASSET_CONTRACT_INTERNAL_ERROR";
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

function pointerToken(value) {
  return String(value).replaceAll("~", "~0").replaceAll("/", "~1");
}

function at(path, token) {
  return `${path}/${pointerToken(token)}`;
}

function diagnostic(phase, code, path) {
  return { phase, severity: "error", code, path, message: code };
}

function compareText(left, right) {
  return left === right ? 0 : left < right ? -1 : 1;
}

function report(diagnostics) {
  const ordered = [...diagnostics].sort((left, right) =>
    (PHASE_ORDER[left.phase] ?? 99) - (PHASE_ORDER[right.phase] ?? 99) ||
    compareText(left.path, right.path) ||
    compareText(left.code, right.code),
  );
  const seen = new Set();
  const stable = [];
  for (const item of ordered) {
    const key = `${item.phase}\u0000${item.code}\u0000${item.path}`;
    if (!seen.has(key)) {
      seen.add(key);
      stable.push(deepFreeze({ ...item }));
    }
  }
  return deepFreeze({
    reportVersion: 1,
    valid: stable.length === 0,
    diagnostics: stable,
  });
}

function rawDepthExceeded(text) {
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
      if (depth > PROTOTYPE_ASSET_LIMITS.documentDepth) {
        return true;
      }
    } else if (character === "}" || character === "]") {
      depth -= 1;
    }
  }
  return false;
}

function duplicateDiagnostics(text) {
  const root = parseTree(text, [], {
    allowTrailingComma: false,
    disallowComments: true,
    allowEmptyContent: false,
  });
  if (!root) {
    return [];
  }
  const output = [];
  const stack = [{ node: root, path: "" }];
  while (stack.length > 0) {
    const { node, path } = stack.pop();
    if (node.type === "object") {
      const keys = new Set();
      for (const property of node.children ?? []) {
        const keyNode = property.children?.[0];
        const valueNode = property.children?.[1];
        if (!keyNode || !valueNode) {
          continue;
        }
        if (keys.has(keyNode.value)) {
          output.push(
            diagnostic(
              "parse",
              "PROTOTYPE_ASSET_BUNDLE_JSON_DUPLICATE_KEY",
              path,
            ),
          );
        } else {
          keys.add(keyNode.value);
        }
        stack.push({ node: valueNode, path: at(path, keyNode.value) });
      }
    } else if (node.type === "array") {
      for (let index = (node.children?.length ?? 0) - 1; index >= 0; index -= 1) {
        stack.push({ node: node.children[index], path: at(path, index) });
      }
    }
  }
  return output;
}

function parseBundle(text) {
  if (typeof text !== "string") {
    return {
      ok: false,
      diagnostics: [
        diagnostic("parse", "PROTOTYPE_ASSET_BUNDLE_JSON_INPUT_TYPE", ""),
      ],
    };
  }
  if (new TextEncoder().encode(text).byteLength > PROTOTYPE_ASSET_LIMITS.manifestBytes) {
    return {
      ok: false,
      diagnostics: [
        diagnostic("parse", "PROTOTYPE_ASSET_BUNDLE_JSON_TOO_LARGE", ""),
      ],
    };
  }
  if (rawDepthExceeded(text)) {
    return {
      ok: false,
      diagnostics: [
        diagnostic("parse", "PROTOTYPE_ASSET_BUNDLE_JSON_DEPTH_EXCEEDED", ""),
      ],
    };
  }
  const parseErrors = [];
  const value = parse(text, parseErrors, {
    allowTrailingComma: false,
    disallowComments: true,
    allowEmptyContent: false,
  });
  if (parseErrors.length > 0 || value === undefined) {
    return {
      ok: false,
      diagnostics: [
        diagnostic("parse", "PROTOTYPE_ASSET_BUNDLE_JSON_SYNTAX", ""),
      ],
    };
  }
  const duplicates = duplicateDiagnostics(text);
  return duplicates.length > 0
    ? { ok: false, diagnostics: duplicates }
    : { ok: true, value };
}

const ajv = new Ajv2020({
  strict: true,
  allErrors: true,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  ownProperties: true,
  validateFormats: false,
  allowUnionTypes: false,
});
const validateStructure = ajv.compile(PROTOTYPE_ASSET_BUNDLE_SCHEMA);

function schemaCode(error) {
  const suffix = {
    required: "REQUIRED",
    additionalProperties: "UNKNOWN_PROPERTY",
    type: "TYPE",
    const: "CONST",
    enum: "ENUM",
    minItems: "MIN_ITEMS",
    maxItems: "MAX_ITEMS",
    uniqueItems: "DUPLICATE_ITEM",
    minLength: "STRING_CONSTRAINT",
    maxLength: "STRING_CONSTRAINT",
    pattern: "STRING_CONSTRAINT",
    minimum: "NUMBER_CONSTRAINT",
    maximum: "NUMBER_CONSTRAINT",
    oneOf: "SHAPE",
    anyOf: "SHAPE",
  }[error.keyword] ?? "INVALID";
  return `PROTOTYPE_ASSET_BUNDLE_SCHEMA_${suffix}`;
}

function schemaPath(error) {
  if (error.keyword === "additionalProperties") {
    return error.instancePath;
  }
  if (error.keyword === "required") {
    const missing = error.params?.missingProperty;
    return typeof missing === "string"
      ? at(error.instancePath, missing)
      : error.instancePath;
  }
  return error.instancePath;
}

function structureDiagnostics(value) {
  if (validateStructure(value)) {
    return [];
  }
  return (validateStructure.errors ?? []).map((error) =>
    diagnostic("schema", schemaCode(error), schemaPath(error)),
  );
}

function isWellFormedString(value) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) {
        return false;
      }
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function textDiagnostics(value) {
  const output = [];
  const stack = [{ value, path: "" }];
  while (stack.length > 0) {
    const current = stack.pop();
    if (typeof current.value === "string") {
      if (!isWellFormedString(current.value)) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_BUNDLE_TEXT_UNPAIRED_SURROGATE",
            current.path,
          ),
        );
      }
    } else if (Array.isArray(current.value)) {
      for (let index = current.value.length - 1; index >= 0; index -= 1) {
        stack.push({ value: current.value[index], path: at(current.path, index) });
      }
    } else if (current.value && typeof current.value === "object") {
      for (const [key, child] of Object.entries(current.value)) {
        if (!isWellFormedString(key)) {
          output.push(
            diagnostic(
              "semantic",
              "PROTOTYPE_ASSET_BUNDLE_TEXT_UNPAIRED_SURROGATE",
              current.path,
            ),
          );
        }
        stack.push({ value: child, path: at(current.path, key) });
      }
    }
  }
  return output;
}

function sameRoleSet(left, right) {
  return left.length === right.length && left.every((role) => right.includes(role));
}

function rolesInCanonicalOrder(roles) {
  return roles.length !== 2 || (roles[0] === "visual" && roles[1] === "collider");
}

function semanticDiagnostics(bundle) {
  const output = [];
  if (bundle.scene.id !== bundle.runtimeIdentity.id) {
    output.push(
      diagnostic(
        "semantic",
        "PROTOTYPE_ASSET_RUNTIME_IDENTITY_MISMATCH",
        "/runtimeIdentity/id",
      ),
    );
  }
  if (bundle.scene.contentVersion !== bundle.runtimeIdentity.contentVersion) {
    output.push(
      diagnostic(
        "semantic",
        "PROTOTYPE_ASSET_RUNTIME_IDENTITY_MISMATCH",
        "/runtimeIdentity/contentVersion",
      ),
    );
  }

  const briefs = new Map();
  let environmentCount = 0;
  for (let index = 0; index < bundle.blueprint.assetBriefs.length; index += 1) {
    const brief = bundle.blueprint.assetBriefs[index];
    const path = `/blueprint/assetBriefs/${index}`;
    if (briefs.has(brief.id)) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_BRIEF_ID_DUPLICATE",
          `${path}/id`,
        ),
      );
    }
    briefs.set(brief.id, brief);
    if (!rolesInCanonicalOrder(brief.roles)) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_ROLE_ORDER_INVALID",
          `${path}/roles`,
        ),
      );
    }
    if (brief.kind === "environment") {
      environmentCount += 1;
      if (
        brief.entityId !== null ||
        !sameRoleSet(brief.roles, ["visual", "collider"])
      ) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_ENVIRONMENT_BRIEF_INVALID",
            path,
          ),
        );
      }
    } else if (brief.entityId === null) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_ENTITY_REQUIRED",
          `${path}/entityId`,
        ),
      );
    }
  }
  if (environmentCount !== 1) {
    output.push(
      diagnostic(
        "semantic",
        "PROTOTYPE_ASSET_ENVIRONMENT_BRIEF_COUNT",
        "/blueprint/assetBriefs",
      ),
    );
  }

  const seenMaterializations = new Set();
  const assetIds = new Set();
  const assetPaths = new Set();
  let fileCount = 0;
  let totalBytes = 0;
  for (let index = 0; index < bundle.materializations.length; index += 1) {
    const item = bundle.materializations[index];
    const path = `/materializations/${index}`;
    const brief = briefs.get(item.assetBriefId);
    if (seenMaterializations.has(item.assetBriefId)) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_MATERIALIZATION_DUPLICATE",
          `${path}/assetBriefId`,
        ),
      );
    }
    seenMaterializations.add(item.assetBriefId);
    if (!brief) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_BRIEF_REFERENCE_NOT_FOUND",
          `${path}/assetBriefId`,
        ),
      );
    } else {
      if (bundle.blueprint.assetBriefs[index]?.id !== item.assetBriefId) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_MATERIALIZATION_ORDER_INVALID",
            `${path}/assetBriefId`,
          ),
        );
      }
      const expectedSource = brief.kind === "environment"
        ? "builtin-template"
        : "meshy-text-to-3d";
      if (item.source.type !== expectedSource) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_SOURCE_KIND_MISMATCH",
            `${path}/source/type`,
          ),
        );
      }
    }

    const materializedRoles = new Set();
    for (let assetIndex = 0; assetIndex < item.assets.length; assetIndex += 1) {
      const asset = item.assets[assetIndex];
      const assetPath = `${path}/assets/${assetIndex}`;
      fileCount += 1;
      totalBytes += asset.byteLength;
      if (assetIds.has(asset.id)) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_FILE_ID_DUPLICATE",
            `${assetPath}/id`,
          ),
        );
      }
      assetIds.add(asset.id);
      if (assetPaths.has(asset.path)) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_FILE_PATH_DUPLICATE",
            `${assetPath}/path`,
          ),
        );
      }
      assetPaths.add(asset.path);
      if (!rolesInCanonicalOrder(asset.roles)) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_ROLE_ORDER_INVALID",
            `${assetPath}/roles`,
          ),
        );
      }
      for (const role of asset.roles) {
        materializedRoles.add(role);
      }
      const expectedProfile = item.source.type === "builtin-template"
        ? PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE
        : PROTOTYPE_ASSET_NORMALIZATION_PROFILE;
      if (asset.normalizationProfile !== expectedProfile) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_NORMALIZATION_PROFILE_MISMATCH",
            `${assetPath}/normalizationProfile`,
          ),
        );
      }
      if (
        asset.roles.includes("collider") &&
        asset.metrics.triangleCount > PROTOTYPE_ASSET_LIMITS.colliderTriangles
      ) {
        output.push(
          diagnostic(
            "semantic",
            "PROTOTYPE_ASSET_COLLIDER_TRIANGLE_LIMIT",
            `${assetPath}/metrics/triangleCount`,
          ),
        );
      }
      for (let axis = 0; axis < 3; axis += 1) {
        if (asset.metrics.boundsMm.min[axis] > asset.metrics.boundsMm.max[axis]) {
          output.push(
            diagnostic(
              "semantic",
              "PROTOTYPE_ASSET_BOUNDS_INVALID",
              `${assetPath}/metrics/boundsMm`,
            ),
          );
          break;
        }
      }
    }
    if (brief && !sameRoleSet([...materializedRoles], brief.roles)) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_ROLE_COVERAGE_MISMATCH",
          `${path}/assets`,
        ),
      );
    }
  }

  for (let index = 0; index < bundle.blueprint.assetBriefs.length; index += 1) {
    if (!seenMaterializations.has(bundle.blueprint.assetBriefs[index].id)) {
      output.push(
        diagnostic(
          "semantic",
          "PROTOTYPE_ASSET_MATERIALIZATION_MISSING",
          `/blueprint/assetBriefs/${index}/id`,
        ),
      );
    }
  }
  if (fileCount > PROTOTYPE_ASSET_LIMITS.files) {
    output.push(
      diagnostic(
        "semantic",
        "PROTOTYPE_ASSET_FILE_COUNT_EXCEEDED",
        "/materializations",
      ),
    );
  }
  if (totalBytes > PROTOTYPE_ASSET_LIMITS.totalAssetBytes) {
    output.push(
      diagnostic(
        "semantic",
        "PROTOTYPE_ASSET_TOTAL_BYTES_EXCEEDED",
        "/materializations",
      ),
    );
  }
  return output;
}

function validateInternal(text) {
  const parsed = parseBundle(text);
  if (!parsed.ok) {
    return report(parsed.diagnostics);
  }
  const structural = structureDiagnostics(parsed.value);
  if (structural.length > 0) {
    return report(structural);
  }
  const malformedText = textDiagnostics(parsed.value);
  if (malformedText.length > 0) {
    return report(malformedText);
  }
  const semantics = semanticDiagnostics(parsed.value);
  if (semantics.length > 0) {
    return report(semantics);
  }
  if (canonicalizeJsonValue(parsed.value) !== text) {
    return report([
      diagnostic(
        "integrity",
        "PROTOTYPE_ASSET_BUNDLE_JSON_NON_CANONICAL",
        "",
      ),
    ]);
  }
  return report([]);
}

export function validatePrototypeAssetBundleJson(text) {
  try {
    return validateInternal(text);
  } catch (error) {
    if (error instanceof PrototypeAssetContractOperationalError) {
      throw error;
    }
    throw new PrototypeAssetContractOperationalError();
  }
}
