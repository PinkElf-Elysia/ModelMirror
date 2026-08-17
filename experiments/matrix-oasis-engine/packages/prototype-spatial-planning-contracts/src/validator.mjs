import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA,
  PROTOTYPE_SPATIAL_INTENT_SCHEMA,
  PROTOTYPE_SPATIAL_PLANNING_LIMITS,
} from "./schema.mjs";

const PHASE_ORDER = Object.freeze({ parse: 0, schema: 1, semantic: 2, integrity: 3 });
const INTERNAL_CODE = "PROTOTYPE_SPATIAL_PLANNING_CONTRACT_INTERNAL_ERROR";

export class PrototypeSpatialPlanningContractOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "PrototypeSpatialPlanningContractOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
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

function compareDiagnostics(left, right) {
  const text = (a, b) => (a === b ? 0 : a < b ? -1 : 1);
  return (
    (PHASE_ORDER[left.phase] ?? 99) - (PHASE_ORDER[right.phase] ?? 99) ||
    text(left.path, right.path) ||
    text(left.code, right.code)
  );
}

function report(items) {
  const seen = new Set();
  const diagnostics = [];
  for (const item of [...items].sort(compareDiagnostics)) {
    const key = `${item.phase}\u0000${item.code}\u0000${item.path}`;
    if (!seen.has(key)) {
      seen.add(key);
      diagnostics.push(deepFreeze({ ...item }));
    }
  }
  return deepFreeze({ reportVersion: 1, valid: diagnostics.length === 0, diagnostics });
}

function rawDepthExceeded(text) {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') inString = true;
    else if (character === "{" || character === "[") {
      depth += 1;
      if (depth > PROTOTYPE_SPATIAL_PLANNING_LIMITS.documentDepth) return true;
    } else if (character === "}" || character === "]") depth -= 1;
  }
  return false;
}

function duplicateDiagnostics(text, prefix) {
  const root = parseTree(text, [], {
    allowTrailingComma: false,
    disallowComments: true,
    allowEmptyContent: false,
  });
  if (!root) return [];
  const output = [];
  const stack = [{ node: root, path: "" }];
  while (stack.length > 0) {
    const { node, path } = stack.pop();
    if (node.type === "object") {
      const keys = new Set();
      for (const property of node.children ?? []) {
        const keyNode = property.children?.[0];
        const valueNode = property.children?.[1];
        if (!keyNode || !valueNode) continue;
        if (keys.has(keyNode.value)) {
          output.push(diagnostic("parse", `${prefix}_JSON_DUPLICATE_KEY`, path));
        } else keys.add(keyNode.value);
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

function parseDocument(text, { prefix, byteLimit }) {
  if (typeof text !== "string") {
    return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_INPUT_TYPE`, "")] };
  }
  if (new TextEncoder().encode(text).byteLength > byteLimit) {
    return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SIZE_EXCEEDED`, "")] };
  }
  if (rawDepthExceeded(text)) {
    return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_DEPTH_EXCEEDED`, "")] };
  }
  const errors = [];
  const value = parse(text, errors, {
    allowTrailingComma: false,
    disallowComments: true,
    allowEmptyContent: false,
  });
  if (errors.length > 0 || value === undefined) {
    return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SYNTAX`, "")] };
  }
  const duplicates = duplicateDiagnostics(text, prefix);
  return duplicates.length > 0 ? { ok: false, diagnostics: duplicates } : { ok: true, value };
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
const validateIntentStructure = ajv.compile(PROTOTYPE_SPATIAL_INTENT_SCHEMA);
const validateFactsStructure = ajv.compile(PROTOTYPE_ENVIRONMENT_FACTS_SCHEMA);

function schemaCode(error, prefix) {
  const suffix =
    ({
      required: "REQUIRED",
      additionalProperties: "UNKNOWN_PROPERTY",
      type: "TYPE",
      const: "CONST",
      enum: "ENUM",
      minItems: "MIN_ITEMS",
      maxItems: "MAX_ITEMS",
      uniqueItems: "DUPLICATE_ITEM",
      minimum: "NUMBER_CONSTRAINT",
      maximum: "NUMBER_CONSTRAINT",
      minLength: "STRING_CONSTRAINT",
      maxLength: "STRING_CONSTRAINT",
      pattern: "STRING_CONSTRAINT",
      oneOf: "SHAPE",
    })[error.keyword] ?? "INVALID";
  return `${prefix}_SCHEMA_${suffix}`;
}

function schemaPath(error) {
  if (error.keyword === "additionalProperties") return error.instancePath;
  if (error.keyword === "required" && typeof error.params?.missingProperty === "string") {
    return at(error.instancePath, error.params.missingProperty);
  }
  return error.instancePath;
}

function structureDiagnostics(value, validate, prefix) {
  if (validate(value)) return [];
  return (validate.errors ?? []).map((error) =>
    diagnostic("schema", schemaCode(error, prefix), schemaPath(error)),
  );
}

function isWellFormedString(value) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function textDiagnostics(value, prefix) {
  const output = [];
  const stack = [{ value, path: "" }];
  while (stack.length > 0) {
    const current = stack.pop();
    if (typeof current.value === "string") {
      if (!isWellFormedString(current.value)) {
        output.push(diagnostic("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, current.path));
      }
    } else if (Array.isArray(current.value)) {
      for (let index = current.value.length - 1; index >= 0; index -= 1) {
        stack.push({ value: current.value[index], path: at(current.path, index) });
      }
    } else if (current.value && typeof current.value === "object") {
      for (const [key, child] of Object.entries(current.value)) {
        if (!isWellFormedString(key)) {
          output.push(diagnostic("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, current.path));
        }
        stack.push({ value: child, path: at(current.path, key) });
      }
    }
  }
  return output;
}

function duplicateFieldDiagnostics(items, field, path, code) {
  const seen = new Set();
  const output = [];
  for (let index = 0; index < items.length; index += 1) {
    const value = items[index][field];
    if (seen.has(value)) output.push(diagnostic("semantic", code, `${path}/${index}/${field}`));
    seen.add(value);
  }
  return output;
}

function intentSemanticDiagnostics(intent) {
  const output = [];
  if (intent.scene.id !== intent.runtime.id) {
    output.push(diagnostic("semantic", "SPATIAL_INTENT_SCENE_ID_MISMATCH", "/scene/id"));
  }
  if (intent.scene.contentVersion !== intent.runtime.contentVersion) {
    output.push(diagnostic("semantic", "SPATIAL_INTENT_CONTENT_VERSION_MISMATCH", "/scene/contentVersion"));
  }
  output.push(
    ...duplicateFieldDiagnostics(intent.zones, "id", "/zones", "SPATIAL_INTENT_ZONE_ID_DUPLICATE"),
    ...duplicateFieldDiagnostics(intent.placements, "id", "/placements", "SPATIAL_INTENT_PLACEMENT_ID_DUPLICATE"),
    ...duplicateFieldDiagnostics(intent.placements, "assetBriefId", "/placements", "SPATIAL_INTENT_ASSET_BRIEF_DUPLICATE"),
    ...duplicateFieldDiagnostics(intent.nodeContexts, "nodeId", "/nodeContexts", "SPATIAL_INTENT_NODE_CONTEXT_DUPLICATE"),
  );
  const zones = new Map(intent.zones.map((item, index) => [item.id, { item, index }]));
  const placements = new Map(intent.placements.map((item, index) => [item.id, { item, index }]));
  for (let zoneIndex = 0; zoneIndex < intent.zones.length; zoneIndex += 1) {
    const zone = intent.zones[zoneIndex];
    for (let adjacentIndex = 0; adjacentIndex < zone.adjacentZoneIds.length; adjacentIndex += 1) {
      const adjacentId = zone.adjacentZoneIds[adjacentIndex];
      const path = `/zones/${zoneIndex}/adjacentZoneIds/${adjacentIndex}`;
      if (adjacentId === zone.id) output.push(diagnostic("semantic", "SPATIAL_INTENT_ZONE_SELF_ADJACENT", path));
      const adjacent = zones.get(adjacentId);
      if (!adjacent) output.push(diagnostic("semantic", "SPATIAL_INTENT_ZONE_REFERENCE_NOT_FOUND", path));
      else if (!adjacent.item.adjacentZoneIds.includes(zone.id)) {
        output.push(diagnostic("semantic", "SPATIAL_INTENT_ZONE_ADJACENCY_ASYMMETRIC", path));
      }
    }
  }
  for (let placementIndex = 0; placementIndex < intent.placements.length; placementIndex += 1) {
    const placement = intent.placements[placementIndex];
    const root = `/placements/${placementIndex}`;
    if (!zones.has(placement.zoneId)) {
      output.push(diagnostic("semantic", "SPATIAL_INTENT_ZONE_REFERENCE_NOT_FOUND", `${root}/zoneId`));
    }
    if (placement.facing.kind === "placement") {
      if (placement.facing.placementId === placement.id) {
        output.push(diagnostic("semantic", "SPATIAL_INTENT_PLACEMENT_SELF_REFERENCE", `${root}/facing/placementId`));
      } else if (!placements.has(placement.facing.placementId)) {
        output.push(diagnostic("semantic", "SPATIAL_INTENT_PLACEMENT_REFERENCE_NOT_FOUND", `${root}/facing/placementId`));
      }
    }
    const nearIds = new Set();
    const separateIds = new Set();
    for (const [field, set] of [["near", nearIds], ["separate", separateIds]]) {
      const constraints = placement[field];
      for (let index = 0; index < constraints.length; index += 1) {
        const target = constraints[index].placementId;
        const path = `${root}/${field}/${index}/placementId`;
        if (target === placement.id) output.push(diagnostic("semantic", "SPATIAL_INTENT_PLACEMENT_SELF_REFERENCE", path));
        else if (!placements.has(target)) output.push(diagnostic("semantic", "SPATIAL_INTENT_PLACEMENT_REFERENCE_NOT_FOUND", path));
        if (set.has(target)) output.push(diagnostic("semantic", "SPATIAL_INTENT_CONSTRAINT_DUPLICATE", path));
        set.add(target);
      }
    }
    for (const target of nearIds) {
      if (separateIds.has(target)) {
        output.push(diagnostic("semantic", "SPATIAL_INTENT_CONSTRAINT_CONFLICT", `${root}/separate`));
      }
    }
  }
  for (let index = 0; index < intent.nodeContexts.length; index += 1) {
    const context = intent.nodeContexts[index];
    const root = `/nodeContexts/${index}`;
    if (!zones.has(context.zoneId)) output.push(diagnostic("semantic", "SPATIAL_INTENT_ZONE_REFERENCE_NOT_FOUND", `${root}/zoneId`));
    for (let visibleIndex = 0; visibleIndex < context.visiblePlacementIds.length; visibleIndex += 1) {
      if (!placements.has(context.visiblePlacementIds[visibleIndex])) {
        output.push(diagnostic("semantic", "SPATIAL_INTENT_PLACEMENT_REFERENCE_NOT_FOUND", `${root}/visiblePlacementIds/${visibleIndex}`));
      }
    }
  }
  return output;
}

function vectorKey(vector) {
  return vector.join(",");
}

function withinBounds(point, bounds) {
  return point.every((value, axis) => value >= bounds.minimumMm[axis] && value <= bounds.maximumMm[axis]);
}

function pointOnPolygonXZ(point, polygon, vertices, floorSnapMm) {
  const polygonVertices = polygon.vertexIndices.map((index) => vertices[index]);
  if (polygonVertices.some((value) => !value)) return false;
  const minY = Math.min(...polygonVertices.map((value) => value[1])) - floorSnapMm;
  const maxY = Math.max(...polygonVertices.map((value) => value[1])) + floorSnapMm;
  if (point[1] < minY || point[1] > maxY) return false;
  let inside = false;
  for (let index = 0, previous = polygonVertices.length - 1; index < polygonVertices.length; previous = index, index += 1) {
    const current = polygonVertices[index];
    const prior = polygonVertices[previous];
    const cross = (point[0] - prior[0]) * (current[2] - prior[2]) - (point[2] - prior[2]) * (current[0] - prior[0]);
    const onSegment = Math.abs(cross) <= 1 && point[0] >= Math.min(prior[0], current[0]) && point[0] <= Math.max(prior[0], current[0]) && point[2] >= Math.min(prior[2], current[2]) && point[2] <= Math.max(prior[2], current[2]);
    if (onSegment) return true;
    const intersects = (current[2] > point[2]) !== (prior[2] > point[2]) && point[0] < ((prior[0] - current[0]) * (point[2] - current[2])) / (prior[2] - current[2]) + current[0];
    if (intersects) inside = !inside;
  }
  return inside;
}

function normalIsUnit(normal) {
  const lengthSquared = normal.reduce((total, value) => total + value * value, 0);
  return lengthSquared >= 900_000_000_000 && lengthSquared <= 1_100_000_000_000;
}

function factsSemanticDiagnostics(facts) {
  const output = [];
  const { source, navigationMesh, environmentBounds } = facts;
  if (source.scene.id !== source.runtime.id) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_SCENE_ID_MISMATCH", "/source/scene/id"));
  if (source.scene.contentVersion !== source.runtime.contentVersion) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_CONTENT_VERSION_MISMATCH", "/source/scene/contentVersion"));
  for (let axis = 0; axis < 3; axis += 1) {
    if (environmentBounds.minimumMm[axis] >= environmentBounds.maximumMm[axis]) {
      output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_BOUNDS_INVALID", "/environmentBounds"));
      break;
    }
  }
  if (navigationMesh.verticesMm.length === 0 || navigationMesh.polygons.length === 0 || navigationMesh.components.length === 0) {
    output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_NAVIGATION_EMPTY", "/navigationMesh"));
    return output;
  }
  const vertexKeys = new Set();
  for (let index = 0; index < navigationMesh.verticesMm.length; index += 1) {
    const vertex = navigationMesh.verticesMm[index];
    const key = vectorKey(vertex);
    if (vertexKeys.has(key)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_VERTEX_DUPLICATE", `/navigationMesh/verticesMm/${index}`));
    vertexKeys.add(key);
    if (!withinBounds(vertex, environmentBounds)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_VERTEX_OUTSIDE_BOUNDS", `/navigationMesh/verticesMm/${index}`));
  }
  const polygonOwners = Array(navigationMesh.polygons.length).fill(-1);
  for (let index = 0; index < navigationMesh.polygons.length; index += 1) {
    const polygon = navigationMesh.polygons[index];
    for (let vertexIndex = 0; vertexIndex < polygon.vertexIndices.length; vertexIndex += 1) {
      if (polygon.vertexIndices[vertexIndex] >= navigationMesh.verticesMm.length) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_POLYGON_VERTEX_INDEX_INVALID", `/navigationMesh/polygons/${index}/vertexIndices/${vertexIndex}`));
    }
    if (polygon.componentIndex >= navigationMesh.components.length) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_POLYGON_COMPONENT_INVALID", `/navigationMesh/polygons/${index}/componentIndex`));
  }
  for (let componentIndex = 0; componentIndex < navigationMesh.components.length; componentIndex += 1) {
    const component = navigationMesh.components[componentIndex];
    if (component.index !== componentIndex) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_COMPONENT_INDEX_UNSTABLE", `/navigationMesh/components/${componentIndex}/index`));
    for (let axis = 0; axis < 3; axis += 1) {
      if (component.bounds.minimumMm[axis] > component.bounds.maximumMm[axis]) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_COMPONENT_BOUNDS_INVALID", `/navigationMesh/components/${componentIndex}/bounds`));
    }
    for (let itemIndex = 0; itemIndex < component.polygonIndices.length; itemIndex += 1) {
      const polygonIndex = component.polygonIndices[itemIndex];
      const path = `/navigationMesh/components/${componentIndex}/polygonIndices/${itemIndex}`;
      if (polygonIndex >= navigationMesh.polygons.length) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_COMPONENT_POLYGON_INDEX_INVALID", path));
      else {
        if (polygonOwners[polygonIndex] !== -1) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_POLYGON_COMPONENT_DUPLICATE", path));
        polygonOwners[polygonIndex] = componentIndex;
        if (navigationMesh.polygons[polygonIndex].componentIndex !== componentIndex) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_POLYGON_COMPONENT_MISMATCH", path));
        for (const vertexIndex of navigationMesh.polygons[polygonIndex].vertexIndices) {
          const vertex = navigationMesh.verticesMm[vertexIndex];
          if (vertex && !withinBounds(vertex, component.bounds)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_COMPONENT_BOUNDS_MISMATCH", `/navigationMesh/components/${componentIndex}/bounds`));
        }
      }
    }
  }
  for (let index = 0; index < polygonOwners.length; index += 1) {
    if (polygonOwners[index] === -1) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_POLYGON_COMPONENT_MISSING", `/navigationMesh/polygons/${index}/componentIndex`));
  }
  output.push(
    ...duplicateFieldDiagnostics(facts.floorAnchors, "id", "/floorAnchors", "ENVIRONMENT_FACTS_ANCHOR_ID_DUPLICATE"),
    ...duplicateFieldDiagnostics(facts.wallAnchors, "id", "/wallAnchors", "ENVIRONMENT_FACTS_ANCHOR_ID_DUPLICATE"),
  );
  const floorIds = new Set(facts.floorAnchors.map((item) => item.id));
  for (let index = 0; index < facts.floorAnchors.length; index += 1) {
    const anchor = facts.floorAnchors[index];
    const root = `/floorAnchors/${index}`;
    if (!withinBounds(anchor.positionMm, environmentBounds)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_ANCHOR_OUTSIDE_BOUNDS", `${root}/positionMm`));
    if (!normalIsUnit(anchor.normalMicros) || anchor.normalMicros[1] < 707_106) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_FLOOR_NORMAL_INVALID", `${root}/normalMicros`));
    const polygon = navigationMesh.polygons[anchor.polygonIndex];
    if (!polygon) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_FLOOR_POLYGON_INVALID", `${root}/polygonIndex`));
    else {
      if (anchor.componentIndex !== polygon.componentIndex) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_FLOOR_COMPONENT_MISMATCH", `${root}/componentIndex`));
      if (!pointOnPolygonXZ(anchor.positionMm, polygon, navigationMesh.verticesMm, facts.analysisProfile.floorSnapMm)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_FLOOR_ANCHOR_OFF_NAVIGATION", `${root}/positionMm`));
    }
  }
  for (let index = 0; index < facts.wallAnchors.length; index += 1) {
    const anchor = facts.wallAnchors[index];
    const root = `/wallAnchors/${index}`;
    if (floorIds.has(anchor.id)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_ANCHOR_ID_DUPLICATE", `${root}/id`));
    if (!withinBounds(anchor.positionMm, environmentBounds)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_ANCHOR_OUTSIDE_BOUNDS", `${root}/positionMm`));
    if (!normalIsUnit(anchor.normalMicros)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_WALL_NORMAL_INVALID", `${root}/normalMicros`));
    if (!floorIds.has(anchor.nearestFloorAnchorId)) output.push(diagnostic("semantic", "ENVIRONMENT_FACTS_FLOOR_ANCHOR_REFERENCE_NOT_FOUND", `${root}/nearestFloorAnchorId`));
  }
  return output;
}

function validateInternal(text, options) {
  const parsed = parseDocument(text, options);
  if (!parsed.ok) return report(parsed.diagnostics);
  const structural = structureDiagnostics(parsed.value, options.validate, options.prefix);
  if (structural.length > 0) return report(structural);
  const malformed = textDiagnostics(parsed.value, options.prefix);
  if (malformed.length > 0) return report(malformed);
  const semantics = options.semantic(parsed.value);
  if (semantics.length > 0) return report(semantics);
  if (canonicalizeJsonValue(parsed.value) !== text) {
    return report([diagnostic("integrity", `${options.prefix}_JSON_NON_CANONICAL`, "")]);
  }
  return report([]);
}

function operational(error) {
  return error instanceof PrototypeSpatialPlanningContractOperationalError
    ? error
    : new PrototypeSpatialPlanningContractOperationalError();
}

export function validatePrototypeSpatialIntentJson(text) {
  try {
    return validateInternal(text, {
      prefix: "PROTOTYPE_SPATIAL_INTENT",
      byteLimit: PROTOTYPE_SPATIAL_PLANNING_LIMITS.intentBytes,
      validate: validateIntentStructure,
      semantic: intentSemanticDiagnostics,
    });
  } catch (error) {
    throw operational(error);
  }
}

export function validatePrototypeEnvironmentFactsJson(text) {
  try {
    return validateInternal(text, {
      prefix: "PROTOTYPE_ENVIRONMENT_FACTS",
      byteLimit: PROTOTYPE_SPATIAL_PLANNING_LIMITS.factsBytes,
      validate: validateFactsStructure,
      semantic: factsSemanticDiagnostics,
    });
  } catch (error) {
    throw operational(error);
  }
}
