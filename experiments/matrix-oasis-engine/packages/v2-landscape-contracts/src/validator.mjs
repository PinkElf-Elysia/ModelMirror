import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  V2_CANDIDATE_CATALOG_SCHEMA,
  V2_CLASS_GATES,
  V2_DECISION_LANDSCAPE_SCHEMA,
  V2_LANDSCAPE_LIMITS,
  V2_LANES,
  V2_ROADMAP_SCHEMA,
  V2_SCORE_LIMITS,
} from "./schema.mjs";

const ajv = new Ajv2020({
  strict: true,
  allErrors: true,
  ownProperties: true,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  validateFormats: false,
});

const validators = Object.freeze({
  catalog: ajv.compile(V2_CANDIDATE_CATALOG_SCHEMA),
  landscape: ajv.compile(V2_DECISION_LANDSCAPE_SCHEMA),
  roadmap: ajv.compile(V2_ROADMAP_SCHEMA),
});

function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

function diagnostic(phase, code, path = "") {
  return { phase, severity: "error", code, path, message: code };
}

function report(diagnostics, value) {
  const seen = new Set();
  const ordered = [];
  for (const entry of [...diagnostics].sort((left, right) => left.path.localeCompare(right.path) || left.code.localeCompare(right.code))) {
    const key = `${entry.path}\0${entry.code}`;
    if (!seen.has(key)) {
      seen.add(key);
      ordered.push(freeze({ ...entry }));
    }
  }
  return freeze({
    reportVersion: 1,
    valid: ordered.length === 0,
    diagnostics: ordered,
    ...(ordered.length === 0 ? { value: freeze(value) } : {}),
  });
}

function strictParse(text, prefix) {
  if (typeof text !== "string") return { diagnostics: [diagnostic("parse", `${prefix}_JSON_INPUT_TYPE`)] };
  if (new TextEncoder().encode(text).byteLength > V2_LANDSCAPE_LIMITS.documentBytes) {
    return { diagnostics: [diagnostic("parse", `${prefix}_JSON_SIZE_EXCEEDED`)] };
  }
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (const character of text) {
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
    } else if (character === '"') inString = true;
    else if (character === "{" || character === "[") {
      depth += 1;
      if (depth > V2_LANDSCAPE_LIMITS.documentDepth) return { diagnostics: [diagnostic("parse", `${prefix}_JSON_DEPTH_EXCEEDED`)] };
    } else if (character === "}" || character === "]") depth -= 1;
  }
  const errors = [];
  const value = parse(text, errors, { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (errors.length > 0 || value === undefined) return { diagnostics: [diagnostic("parse", `${prefix}_JSON_SYNTAX`)] };
  const tree = parseTree(text, [], { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  const stack = tree ? [{ node: tree, path: "" }] : [];
  const duplicates = [];
  while (stack.length > 0) {
    const { node, path } = stack.pop();
    if (node.type === "object") {
      const keys = new Set();
      for (const property of node.children ?? []) {
        const key = property.children?.[0]?.value;
        const child = property.children?.[1];
        if (typeof key !== "string" || !child) continue;
        if (keys.has(key)) duplicates.push(diagnostic("parse", `${prefix}_JSON_DUPLICATE_KEY`, path));
        keys.add(key);
        stack.push({ node: child, path: `${path}/${key.replaceAll("~", "~0").replaceAll("/", "~1")}` });
      }
    } else if (node.type === "array") {
      (node.children ?? []).forEach((child, index) => stack.push({ node: child, path: `${path}/${index}` }));
    }
  }
  return duplicates.length > 0 ? { diagnostics: duplicates } : { value };
}

function wellFormed(value) {
  const stack = [value];
  while (stack.length > 0) {
    const current = stack.pop();
    if (typeof current === "string") {
      for (let index = 0; index < current.length; index += 1) {
        const unit = current.charCodeAt(index);
        if (unit >= 0xd800 && unit <= 0xdbff) {
          const next = current.charCodeAt(index + 1);
          if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
          index += 1;
        } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
      }
    } else if (Array.isArray(current)) stack.push(...current);
    else if (current && typeof current === "object") stack.push(...Object.keys(current), ...Object.values(current));
  }
  return true;
}

function schemaDiagnostics(validator, prefix) {
  const suffix = {
    required: "REQUIRED",
    additionalProperties: "UNKNOWN_PROPERTY",
    type: "TYPE",
    const: "CONST",
    enum: "ENUM",
    anyOf: "UNION",
    pattern: "STRING_CONSTRAINT",
    minLength: "STRING_CONSTRAINT",
    maxLength: "STRING_CONSTRAINT",
    minimum: "NUMBER_CONSTRAINT",
    maximum: "NUMBER_CONSTRAINT",
    uniqueItems: "ARRAY_CONSTRAINT",
    minItems: "ARRAY_CONSTRAINT",
    maxItems: "ARRAY_CONSTRAINT",
  };
  return (validator.errors ?? []).map((error) => diagnostic(
    "schema",
    `${prefix}_SCHEMA_${suffix[error.keyword] ?? "INVALID"}`,
    error.keyword === "required" ? `${error.instancePath}/${error.params.missingProperty}` : error.instancePath,
  ));
}

function catalogSemantics(value) {
  const diagnostics = [];
  const lanes = value.catalog.lanes;
  const candidates = value.catalog.candidates;
  const laneById = new Map();
  const candidateById = new Map();
  lanes.forEach((lane, index) => {
    if (laneById.has(lane.id)) diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_LANE_DUPLICATE", `/catalog/lanes/${index}/id`));
    laneById.set(lane.id, lane);
    if (lane.id === "creator-commercial-benchmark" ? lane.executable : !lane.executable) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_LANE_EXECUTION_CLASS", `/catalog/lanes/${index}/executable`));
    }
  });
  if (V2_LANES.some((id) => !laneById.has(id))) diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_LANES_INCOMPLETE", "/catalog/lanes"));
  candidates.forEach((candidate, index) => {
    if (candidateById.has(candidate.id)) diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_CANDIDATE_DUPLICATE", `/catalog/candidates/${index}/id`));
    candidateById.set(candidate.id, candidate);
    if (candidate.staticExclusion.excluded !== (candidate.staticExclusion.code !== null)) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_EXCLUSION_INCONSISTENT", `/catalog/candidates/${index}/staticExclusion`));
    }
    if (candidate.license.reuseAllowed !== (candidate.license.closureStatus === "approved")) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_LICENSE_INCONSISTENT", `/catalog/candidates/${index}/license`));
    }
    if (candidate.license.qualificationAllowed !== ["approved", "direct-approved"].includes(candidate.license.closureStatus)) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_LICENSE_QUALIFICATION_INCONSISTENT", `/catalog/candidates/${index}/license`));
    }
    const git = candidate.source.kind === "git-repository";
    if (git !== (candidate.source.location.host === "github.com" && candidate.source.commit !== null && candidate.source.gitTreeSha1 !== null)) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_SOURCE_IDENTITY_INCONSISTENT", `/catalog/candidates/${index}/source`));
    }
    const gitReference = candidate.source.kind === "git-reference";
    if (gitReference !== (candidate.source.location.host === "github.com" && candidate.source.commit !== null && candidate.source.gitTreeSha1 === null)) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_SOURCE_REFERENCE_INCONSISTENT", `/catalog/candidates/${index}/source`));
    }
    const searchResult = candidate.source.kind === "github-search-result";
    if (searchResult && (candidate.source.location.host !== "github.com" || candidate.source.commit !== null || candidate.source.gitTreeSha1 !== null)) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_SEARCH_IDENTITY_INCONSISTENT", `/catalog/candidates/${index}/source`));
    }
    if (candidate.source.kind === "source-archive" && candidate.source.archiveSha256 === null) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_ARCHIVE_IDENTITY_MISSING", `/catalog/candidates/${index}/source/archiveSha256`));
    }
    if (!["git-repository", "git-reference", "source-archive"].includes(candidate.source.kind) && (candidate.source.commit !== null || candidate.source.gitTreeSha1 !== null || candidate.source.archiveSha256 !== null)) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_SOURCE_IDENTITY_UNEXPECTED", `/catalog/candidates/${index}/source`));
    }
    if (candidate.candidateType === "commercial-benchmark" && candidate.source.kind !== "public-documentation") {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_COMMERCIAL_SOURCE_INVALID", `/catalog/candidates/${index}/source/kind`));
    }
  });
  lanes.forEach((lane, laneIndex) => {
    const members = lane.candidateIds.map((id) => candidateById.get(id));
    if (members.some((candidate) => !candidate || !candidate.laneIds.includes(lane.id))) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_LANE_MEMBER_INVALID", `/catalog/lanes/${laneIndex}/candidateIds`));
    }
    if (members.filter((candidate) => candidate && ["open-source", "internal-baseline"].includes(candidate.candidateType)).length < 4) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_OPEN_OR_INTERNAL_QUOTA", `/catalog/lanes/${laneIndex}/candidateIds`));
    }
    if (members.filter((candidate) => candidate?.newSinceR17).length < 2) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_NEW_CANDIDATE_QUOTA", `/catalog/lanes/${laneIndex}/candidateIds`));
    }
  });
  candidates.forEach((candidate, index) => {
    if (candidate.laneIds.some((lane) => !laneById.get(lane)?.candidateIds.includes(candidate.id))) {
      diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_CANDIDATE_LANE_INVALID", `/catalog/candidates/${index}/laneIds`));
    }
  });
  const entries = lanes.reduce((sum, lane) => sum + lane.candidateIds.length, 0);
  if (entries < 48) diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_TOTAL_ENTRY_QUOTA", "/catalog/lanes"));
  const commercial = candidates.filter((candidate) => candidate.candidateType === "commercial-benchmark" && candidate.laneIds.includes("creator-commercial-benchmark"));
  if (commercial.length < 4) diagnostics.push(diagnostic("semantic", "V2_CANDIDATE_CATALOG_COMMERCIAL_QUOTA", "/catalog/candidates"));
  return diagnostics;
}

function landscapeSemantics(value) {
  const diagnostics = [];
  const pairs = new Set();
  const ranks = new Set();
  const lanes = new Set();
  const executedCandidates = new Set();
  value.decisions.forEach((decision, index) => {
    const pair = `${decision.laneId}\0${decision.candidateId}`;
    if (pairs.has(pair)) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_DECISION_DUPLICATE", `/decisions/${index}`));
    pairs.add(pair);
    lanes.add(decision.laneId);
    const total = Object.keys(V2_SCORE_LIMITS).reduce((sum, key) => sum + decision.scores[key], 0);
    if (decision.total !== total) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_TOTAL_MISMATCH", `/decisions/${index}/total`));
    const gateIds = new Set();
    for (const gate of decision.hardGates) {
      if (gateIds.has(gate.id)) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_GATE_DUPLICATE", `/decisions/${index}/hardGates`));
      gateIds.add(gate.id);
      if ((gate.status === "fail") !== (gate.code !== null)) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_GATE_CODE_INCONSISTENT", `/decisions/${index}/hardGates`));
    }
    const requiredGates = V2_CLASS_GATES[decision.qualificationClass];
    if (requiredGates.length !== gateIds.size || requiredGates.some((gate) => !gateIds.has(gate))) {
      diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_GATE_SET_INVALID", `/decisions/${index}/hardGates`));
    }
    if (["executed", "failed", "evidence-gap"].includes(decision.executionStatus)) executedCandidates.add(decision.candidateId);
    if (decision.shortlistRank !== null) {
      const rankKey = `${decision.laneId}\0${decision.shortlistRank}`;
      if (ranks.has(rankKey)) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_SHORTLIST_RANK_DUPLICATE", `/decisions/${index}/shortlistRank`));
      ranks.add(rankKey);
      if (decision.tier === "architecture-reference" || decision.total < 70) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_SHORTLIST_INCONSISTENT", `/decisions/${index}/shortlistRank`));
    }
    if (decision.qualificationClass === "commercial" && decision.tier !== "architecture-reference") {
      diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_COMMERCIAL_TIER_INVALID", `/decisions/${index}/tier`));
    }
    if (decision.tier === "integration-recommended" && (decision.total < 80 || decision.hardGates.some((gate) => gate.status !== "pass"))) {
      diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_INTEGRATION_GATE_INVALID", `/decisions/${index}/tier`));
    }
    if (decision.executionStatus === "failed" && decision.harnessAttribution === "unresolved" && decision.conclusion === "rejected") {
      diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_EVIDENCE_GAP_MISCLASSIFIED", `/decisions/${index}/conclusion`));
    }
    if ((decision.exclusionCode !== null) !== (decision.conclusion === "rejected")) {
      diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_EXCLUSION_INCONSISTENT", `/decisions/${index}/exclusionCode`));
    }
  });
  if (V2_LANES.some((lane) => !lanes.has(lane))) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_LANES_INCOMPLETE", "/decisions"));
  if (executedCandidates.size < 12 || executedCandidates.size > 16) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_EXECUTION_QUOTA", "/decisions"));
  for (const lane of V2_LANES.slice(0, -1)) {
    const ranksForLane = value.decisions.filter((decision) => decision.laneId === lane && decision.shortlistRank !== null).map((decision) => decision.shortlistRank).sort((left, right) => left - right);
    const count = ranksForLane.length;
    if (count < 2 || count > 3) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_SHORTLIST_QUOTA", "/decisions"));
    else if (ranksForLane.some((rank, index) => rank !== index + 1)) diagnostics.push(diagnostic("semantic", "V2_DECISION_LANDSCAPE_SHORTLIST_RANK_GAP", "/decisions"));
  }
  return diagnostics;
}

function roadmapSemantics(value) {
  const diagnostics = [];
  const expected = ["R19", "R20", "R21", "R22", "R23", "R24", "R25"];
  value.rounds.forEach((round, index) => {
    if (round.id !== expected[index]) diagnostics.push(diagnostic("semantic", "V2_ROADMAP_ROUND_ORDER_INVALID", `/rounds/${index}/id`));
    const allowed = new Set(["R18", ...expected.slice(0, index)]);
    if (round.dependsOn.some((dependency) => !allowed.has(dependency))) diagnostics.push(diagnostic("semantic", "V2_ROADMAP_DEPENDENCY_ORDER_INVALID", `/rounds/${index}/dependsOn`));
    if (index === 0 ? !round.dependsOn.includes("R18") : !round.dependsOn.includes(expected[index - 1])) {
      diagnostics.push(diagnostic("semantic", "V2_ROADMAP_DEPENDENCY_INCOMPLETE", `/rounds/${index}/dependsOn`));
    }
  });
  return diagnostics;
}

const definitions = Object.freeze({
  catalog: { prefix: "V2_CANDIDATE_CATALOG", semantics: catalogSemantics },
  landscape: { prefix: "V2_DECISION_LANDSCAPE", semantics: landscapeSemantics },
  roadmap: { prefix: "V2_ROADMAP", semantics: roadmapSemantics },
});

function validate(text, kind) {
  const { prefix, semantics } = definitions[kind];
  try {
    const parsed = strictParse(text, prefix);
    if (parsed.diagnostics) return report(parsed.diagnostics);
    if (!wellFormed(parsed.value)) return report([diagnostic("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`)]);
    const schemaValidator = validators[kind];
    if (!schemaValidator(parsed.value)) return report(schemaDiagnostics(schemaValidator, prefix));
    const semanticDiagnostics = semantics(parsed.value);
    if (semanticDiagnostics.length > 0) return report(semanticDiagnostics);
    if (canonicalizeJsonValue(parsed.value) !== text) return report([diagnostic("integrity", `${prefix}_JSON_NON_CANONICAL`)]);
    return report([], parsed.value);
  } catch {
    return report([diagnostic("operation", `${prefix}_INTERNAL_ERROR`)]);
  }
}

export function validateV2CandidateCatalogJson(text) {
  return validate(text, "catalog");
}

export function validateV2DecisionLandscapeJson(text) {
  return validate(text, "landscape");
}

export function validateV2RoadmapJson(text) {
  return validate(text, "roadmap");
}
