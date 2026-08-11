import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { validateAuthoringGamePackJson } from "@matrix-oasis/game-pack-validator";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  GENERATION_PROPOSAL_SCHEMA,
  PROTOTYPE_GENERATION_LIMITS,
} from "./schema.mjs";

const PHASE_ORDER = Object.freeze({ parse: 0, schema: 1, semantic: 2 });

export class PrototypeGenerationContractOperationalError extends Error {
  constructor() {
    super("PROTOTYPE_GENERATION_CONTRACT_INTERNAL_ERROR");
    this.name = "PrototypeGenerationContractOperationalError";
    this.code = "PROTOTYPE_GENERATION_CONTRACT_INTERNAL_ERROR";
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

function diagnostic(phase, code, path, extras = {}) {
  return {
    phase,
    severity: "error",
    code,
    path,
    message: code,
    ...extras,
  };
}

function compareDiagnostics(left, right) {
  const compareText = (leftText, rightText) =>
    leftText === rightText ? 0 : leftText < rightText ? -1 : 1;
  return (
    (PHASE_ORDER[left.phase] ?? 99) - (PHASE_ORDER[right.phase] ?? 99) ||
    compareText(left.path, right.path) ||
    compareText(left.code, right.code)
  );
}

function report(diagnostics) {
  const seen = new Set();
  const stable = [];
  for (const item of [...diagnostics].sort(compareDiagnostics)) {
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
      if (depth > PROTOTYPE_GENERATION_LIMITS.documentDepth) {
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
            diagnostic("parse", "PROTOTYPE_PROPOSAL_JSON_DUPLICATE_KEY", path),
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

function parseProposal(text) {
  if (typeof text !== "string") {
    return {
      ok: false,
      diagnostics: [
        diagnostic("parse", "PROTOTYPE_PROPOSAL_JSON_INPUT_TYPE", ""),
      ],
    };
  }
  if (rawDepthExceeded(text)) {
    return {
      ok: false,
      diagnostics: [
        diagnostic("parse", "PROTOTYPE_PROPOSAL_JSON_DEPTH_EXCEEDED", ""),
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
        diagnostic("parse", "PROTOTYPE_PROPOSAL_JSON_SYNTAX", ""),
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
const validateStructure = ajv.compile(GENERATION_PROPOSAL_SCHEMA);

function schemaCode(error) {
  const suffix =
    {
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
      oneOf: "SHAPE",
      anyOf: "SHAPE",
    }[error.keyword] ?? "INVALID";
  return `PROTOTYPE_PROPOSAL_SCHEMA_${suffix}`;
}

function schemaPath(error) {
  if (error.keyword === "additionalProperties") {
    return error.instancePath;
  }
  if (error.keyword === "required") {
    const missing = error.params?.missingProperty;
    return typeof missing === "string" ? at(error.instancePath, missing) : error.instancePath;
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

function prefixedAuthoringDiagnostics(authoringJson) {
  const authoringReport = validateAuthoringGamePackJson(authoringJson);
  return authoringReport.diagnostics.map((item) => {
    const output = diagnostic(
      item.phase,
      item.code,
      `/authoringGamePack${item.path}`,
    );
    if (typeof item.relatedPath === "string") {
      output.relatedPath = `/authoringGamePack${item.relatedPath}`;
    }
    if (item.location) {
      output.location = deepFreeze({
        line: item.location.line,
        column: item.location.column,
      });
    }
    return output;
  });
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
            "PROTOTYPE_PROPOSAL_TEXT_UNPAIRED_SURROGATE",
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
              "PROTOTYPE_PROPOSAL_TEXT_UNPAIRED_SURROGATE",
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

function duplicateIdDiagnostics(items, path, code, globalIds) {
  const output = [];
  const localIds = new Set();
  for (let index = 0; index < items.length; index += 1) {
    const id = items[index].id;
    if (localIds.has(id) || globalIds.has(id)) {
      output.push(diagnostic("semantic", code, `${path}/${index}/id`));
    }
    localIds.add(id);
    globalIds.add(id);
  }
  return output;
}

function semanticDiagnostics(proposal) {
  const output = [];
  const authoring = proposal.authoringGamePack;
  const blueprint = proposal.sceneBlueprint;
  if (blueprint.scene.id !== authoring.id) {
    output.push(
      diagnostic(
        "semantic",
        "SCENE_BLUEPRINT_SCENE_ID_MISMATCH",
        "/sceneBlueprint/scene/id",
      ),
    );
  }
  if (blueprint.scene.contentVersion !== authoring.contentVersion) {
    output.push(
      diagnostic(
        "semantic",
        "SCENE_BLUEPRINT_CONTENT_VERSION_MISMATCH",
        "/sceneBlueprint/scene/contentVersion",
      ),
    );
  }

  const globalIds = new Set();
  output.push(
    ...duplicateIdDiagnostics(
      blueprint.zones,
      "/sceneBlueprint/zones",
      "SCENE_BLUEPRINT_ZONE_ID_DUPLICATE",
      globalIds,
    ),
    ...duplicateIdDiagnostics(
      blueprint.assetBriefs,
      "/sceneBlueprint/assetBriefs",
      "SCENE_BLUEPRINT_ASSET_BRIEF_ID_DUPLICATE",
      globalIds,
    ),
    ...duplicateIdDiagnostics(
      blueprint.placements,
      "/sceneBlueprint/placements",
      "SCENE_BLUEPRINT_PLACEMENT_ID_DUPLICATE",
      globalIds,
    ),
  );

  const zones = new Set(blueprint.zones.map((item) => item.id));
  const assets = new Map(blueprint.assetBriefs.map((item) => [item.id, item]));
  const placements = new Map(blueprint.placements.map((item) => [item.id, item]));
  const entityIds = new Set(authoring.entities.map((item) => item.id));
  const nodeIds = new Set(authoring.nodes.map((item) => item.id));
  const environmentBriefs = blueprint.assetBriefs.filter(
    (item) => item.kind === "environment",
  );
  if (environmentBriefs.length !== 1) {
    output.push(
      diagnostic(
        "semantic",
        "SCENE_BLUEPRINT_ENVIRONMENT_BRIEF_COUNT",
        "/sceneBlueprint/assetBriefs",
      ),
    );
  }
  for (let index = 0; index < blueprint.assetBriefs.length; index += 1) {
    const item = blueprint.assetBriefs[index];
    const path = `/sceneBlueprint/assetBriefs/${index}`;
    if (item.kind === "environment") {
      if (item.entityId !== null) {
        output.push(
          diagnostic("semantic", "SCENE_BLUEPRINT_ENVIRONMENT_ENTITY_FORBIDDEN", `${path}/entityId`),
        );
      }
      if (!(item.roles.includes("visual") && item.roles.includes("collider"))) {
        output.push(
          diagnostic("semantic", "SCENE_BLUEPRINT_ENVIRONMENT_ROLES_INVALID", `${path}/roles`),
        );
      }
    } else if (item.entityId === null || !entityIds.has(item.entityId)) {
      output.push(
        diagnostic("semantic", "SCENE_BLUEPRINT_ASSET_ENTITY_REFERENCE_INVALID", `${path}/entityId`),
      );
    }
  }

  let environmentPlacementCount = 0;
  for (let index = 0; index < blueprint.placements.length; index += 1) {
    const item = blueprint.placements[index];
    const path = `/sceneBlueprint/placements/${index}`;
    const asset = assets.get(item.assetBriefId);
    if (!asset) {
      output.push(
        diagnostic("semantic", "SCENE_BLUEPRINT_ASSET_REFERENCE_NOT_FOUND", `${path}/assetBriefId`),
      );
    } else {
      if (asset.kind === "environment") {
        environmentPlacementCount += 1;
      }
      if (item.entityId !== asset.entityId) {
        output.push(
          diagnostic("semantic", "SCENE_BLUEPRINT_PLACEMENT_ENTITY_MISMATCH", `${path}/entityId`),
        );
      }
    }
    if (!zones.has(item.zoneId)) {
      output.push(
        diagnostic("semantic", "SCENE_BLUEPRINT_ZONE_REFERENCE_NOT_FOUND", `${path}/zoneId`),
      );
    }
  }
  if (environmentPlacementCount !== 1) {
    output.push(
      diagnostic(
        "semantic",
        "SCENE_BLUEPRINT_ENVIRONMENT_PLACEMENT_COUNT",
        "/sceneBlueprint/placements",
      ),
    );
  }

  const environmentPlacement = blueprint.placements.find(
    (item) => assets.get(item.assetBriefId)?.kind === "environment",
  );
  const boundNodes = new Set();
  for (let index = 0; index < blueprint.nodeBindings.length; index += 1) {
    const item = blueprint.nodeBindings[index];
    const path = `/sceneBlueprint/nodeBindings/${index}`;
    if (boundNodes.has(item.nodeId)) {
      output.push(
        diagnostic("semantic", "SCENE_BLUEPRINT_NODE_BINDING_DUPLICATE", `${path}/nodeId`),
      );
    }
    boundNodes.add(item.nodeId);
    if (!nodeIds.has(item.nodeId)) {
      output.push(
        diagnostic("semantic", "SCENE_BLUEPRINT_NODE_REFERENCE_NOT_FOUND", `${path}/nodeId`),
      );
    }
    if (!zones.has(item.zoneId)) {
      output.push(
        diagnostic("semantic", "SCENE_BLUEPRINT_ZONE_REFERENCE_NOT_FOUND", `${path}/zoneId`),
      );
    }
    for (let placementIndex = 0; placementIndex < item.visiblePlacementIds.length; placementIndex += 1) {
      if (!placements.has(item.visiblePlacementIds[placementIndex])) {
        output.push(
          diagnostic(
            "semantic",
            "SCENE_BLUEPRINT_PLACEMENT_REFERENCE_NOT_FOUND",
            `${path}/visiblePlacementIds/${placementIndex}`,
          ),
        );
      }
    }
    if (
      environmentPlacement &&
      !item.visiblePlacementIds.includes(environmentPlacement.id)
    ) {
      output.push(
        diagnostic(
          "semantic",
          "SCENE_BLUEPRINT_ENVIRONMENT_NOT_VISIBLE",
          `${path}/visiblePlacementIds`,
        ),
      );
    }
  }
  for (let index = 0; index < authoring.nodes.length; index += 1) {
    if (!boundNodes.has(authoring.nodes[index].id)) {
      output.push(
        diagnostic(
          "semantic",
          "SCENE_BLUEPRINT_NODE_BINDING_MISSING",
          `/authoringGamePack/nodes/${index}/id`,
        ),
      );
    }
  }
  return output;
}

function validateInternal(text, includeValue) {
  const parsed = parseProposal(text);
  if (!parsed.ok) {
    return { report: report(parsed.diagnostics) };
  }
  const structural = structureDiagnostics(parsed.value);
  if (structural.length > 0) {
    return { report: report(structural) };
  }
  const malformedText = textDiagnostics(parsed.value);
  if (malformedText.length > 0) {
    return { report: report(malformedText) };
  }
  const authoringJson = canonicalizeJsonValue(parsed.value.authoringGamePack);
  const authoringDiagnostics = prefixedAuthoringDiagnostics(authoringJson);
  if (authoringDiagnostics.length > 0) {
    return { report: report(authoringDiagnostics) };
  }
  const semantics = semanticDiagnostics(parsed.value);
  const validationReport = report(semantics);
  if (!includeValue || !validationReport.valid) {
    return { report: validationReport };
  }
  const canonicalProposalJson = canonicalizeJsonValue(parsed.value);
  const canonicalSceneBlueprintJson = canonicalizeJsonValue(
    parsed.value.sceneBlueprint,
  );
  return {
    report: validationReport,
    value: deepFreeze(parsed.value),
    canonicalProposalJson,
    canonicalAuthoringJson: authoringJson,
    canonicalSceneBlueprintJson,
  };
}

function operational(error) {
  if (error instanceof PrototypeGenerationContractOperationalError) {
    return error;
  }
  return new PrototypeGenerationContractOperationalError();
}

export function validateGenerationProposalJson(text) {
  try {
    return validateInternal(text, false).report;
  } catch (error) {
    throw operational(error);
  }
}

export function prepareGenerationProposalJson(text) {
  try {
    const result = validateInternal(text, true);
    if (!result.report.valid) {
      return deepFreeze({ ok: false, validationReport: result.report });
    }
    return deepFreeze({
      ok: true,
      value: result.value,
      canonicalProposalJson: result.canonicalProposalJson,
      canonicalAuthoringJson: result.canonicalAuthoringJson,
      canonicalSceneBlueprintJson: result.canonicalSceneBlueprintJson,
      validationReport: result.report,
    });
  } catch (error) {
    throw operational(error);
  }
}
