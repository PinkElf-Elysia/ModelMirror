import { createHash } from "node:crypto";

import {
  SKILL_RUNTIME_RANKER_VERSION,
  fingerprintSkillRuntimeIndex,
} from "./skill-runtime-index.mjs";

export const SKILL_SEARCH_INDEX_VERSION = 1;
export const SKILL_SEMANTIC_DOCUMENT_VERSION = "skill-semantic-document-v1";
export const MAX_SEMANTIC_DOCUMENT_CHARACTERS = 1200;

const RUNTIME_COMPARABLE_KEYS = [
  "candidateId",
  "sourceType",
  "targetType",
  "sourceId",
  "name",
  "category",
  "kind",
  "description",
  "sourceDescription",
  "searchDescription",
  "tags",
  "includedSkills",
  "pathTerms",
  "parentNames",
  "publisher",
  "sourceGroup",
  "parentSkillSets",
  "installSource",
  "directoryTreeSha",
];

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort((left, right) => left.localeCompare(right, "en"))
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function fingerprintPayload(value) {
  return sha256(canonicalJson(value));
}

function boundedText(value, limit) {
  return Array.from(
    String(value ?? "")
      .normalize("NFC")
      .replace(/\s+/gu, " ")
      .trim(),
  )
    .slice(0, limit)
    .join("");
}

function installSource(target) {
  const source =
    target.targetType === "member" ? target.installSource : target.project.installSource;
  if (!source) return null;
  return {
    repoUrl: source.repoUrl,
    subPath: source.subPath,
    verifiedCommit: source.verifiedCommit.toLowerCase(),
  };
}

function parentSkillSets(target) {
  return target.targetType === "member"
    ? target.parentSkillSets.map((project) => ({ id: project.id, name: project.name }))
    : [];
}

function runtimeDescriptor(target) {
  const source = installSource(target);
  return {
    candidateId: `catalog:${target.targetType}:${target.id}`,
    sourceType: "catalog",
    targetType: target.targetType,
    sourceId: target.id,
    name: target.name,
    category: target.category,
    kind: target.kind,
    description: target.description,
    sourceDescription: target.sourceDescription ?? "",
    searchDescription: target.searchDescription ?? target.sourceDescription ?? "",
    tags: [...target.tags],
    includedSkills: [...(target.includedSkills ?? [])],
    pathTerms: [...(target.pathTerms ?? [])],
    parentNames: [...(target.parentNames ?? [])],
    publisher: target.publisher ?? "",
    sourceGroup: target.sourceGroup ?? "",
    parentSkillSets: parentSkillSets(target),
    installSource: source,
    directoryTreeSha:
      target.targetType === "member" ? target.member.directoryTreeSha : null,
  };
}

function publicDescriptor(target) {
  const descriptor = runtimeDescriptor(target);
  return {
    ...descriptor,
    name: boundedText(descriptor.name, 240),
    category: boundedText(descriptor.category, 120),
    description: boundedText(descriptor.description, 600),
    sourceDescription: boundedText(descriptor.sourceDescription, 600),
    searchDescription: boundedText(descriptor.searchDescription, 600),
    tags: descriptor.tags.map((value) => boundedText(value, 120)).slice(0, 32),
    includedSkills: descriptor.includedSkills
      .map((value) => boundedText(value, 160))
      .slice(0, 64),
    pathTerms: descriptor.pathTerms
      .map((value) => boundedText(value, 120))
      .slice(0, 64),
    parentNames: descriptor.parentNames
      .map((value) => boundedText(value, 160))
      .slice(0, 32),
    publisher: boundedText(descriptor.publisher, 160),
    sourceGroup: boundedText(descriptor.sourceGroup, 160),
  };
}

function semanticDocument(descriptor) {
  const capability = descriptor.description;
  const triggerBoundary =
    descriptor.searchDescription && descriptor.searchDescription !== capability
      ? descriptor.searchDescription
      : [descriptor.tags.join("、"), descriptor.includedSkills.join("、")]
          .filter(Boolean)
          .join("；");
  const document = boundedText(
    [
      `名称：${descriptor.name}`,
      `类别：${descriptor.category}`,
      descriptor.tags.length ? `标签：${descriptor.tags.join("、")}` : "",
      capability ? `能力：${capability}` : "",
      triggerBoundary ? `触发与边界：${triggerBoundary}` : "",
      descriptor.parentNames.length
        ? `父集合：${descriptor.parentNames.join("、")}`
        : "",
    ]
      .filter(Boolean)
      .join("\n"),
    MAX_SEMANTIC_DOCUMENT_CHARACTERS,
  );
  if (
    /-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bghp_[A-Za-z0-9]{36}\b|\bsk-[A-Za-z0-9_-]{20,}\b/u.test(
      document,
    )
  ) {
    throw new Error("Skill Search semantic document contains credential-like content.");
  }
  return document;
}

function trustIndexFingerprint(trustIndex) {
  const payload = Object.fromEntries(
    Object.entries(trustIndex).filter(([key]) => key !== "fingerprint"),
  );
  return fingerprintPayload(payload);
}

function runtimeComparable(candidate) {
  return Object.fromEntries(RUNTIME_COMPARABLE_KEYS.map((key) => [key, candidate[key]]));
}

export function buildSkillSearchIndex({
  candidates,
  memberIndexFingerprint,
  runtimeIndex,
  trustIndex,
}) {
  if (
    fingerprintSkillRuntimeIndex(runtimeIndex) !== runtimeIndex.fingerprint ||
    trustIndexFingerprint(trustIndex) !== trustIndex.fingerprint ||
    runtimeIndex.trustIndexFingerprint !== trustIndex.fingerprint ||
    runtimeIndex.catalogFingerprint !== trustIndex.catalogFingerprint ||
    runtimeIndex.memberIndexFingerprint !== memberIndexFingerprint
  ) {
    throw new Error("Skill Search, Runtime, and Trust indexes do not share one directory state.");
  }

  const runtimeById = new Map(
    runtimeIndex.candidates.map((candidate) => [candidate.candidateId, candidate]),
  );
  const nameOrder = new Map(
    [...candidates]
      .sort(
        (left, right) =>
          left.name.localeCompare(right.name, "zh-CN") ||
          `catalog:${left.targetType}:${left.id}`.localeCompare(
            `catalog:${right.targetType}:${right.id}`,
            "en",
          ),
      )
      .map((candidate, index) => [
        `catalog:${candidate.targetType}:${candidate.id}`,
        index,
      ]),
  );
  const seen = new Set();
  const searchCandidates = candidates
    .map((target) => {
      const descriptor = publicDescriptor(target);
      const exactDescriptor = runtimeDescriptor(target);
      if (seen.has(descriptor.candidateId)) {
        throw new Error(`Skill Search index contains duplicate candidate ${descriptor.candidateId}.`);
      }
      seen.add(descriptor.candidateId);
      const runtimeCandidate = runtimeById.get(descriptor.candidateId);
      if (
        runtimeCandidate &&
        canonicalJson(runtimeComparable(runtimeCandidate)) !==
          canonicalJson(runtimeComparable(exactDescriptor))
      ) {
        throw new Error(
          `Skill Search candidate does not match Runtime candidate ${descriptor.candidateId}.`,
        );
      }
      const payload = {
        ...descriptor,
        installStatus: target.installStatus,
        deprecated: Boolean(target.deprecated),
        semanticDocument: semanticDocument(descriptor),
        runtimeCandidateFingerprint: runtimeCandidate?.candidateFingerprint ?? null,
        trustFingerprint: runtimeCandidate?.trust?.trustFingerprint ?? null,
        stableNameOrder: nameOrder.get(descriptor.candidateId),
      };
      return {
        ...payload,
        candidateFingerprint: fingerprintPayload(payload),
      };
    })
    .sort((left, right) => left.candidateId.localeCompare(right.candidateId, "en"));

  for (const runtimeCandidate of runtimeIndex.candidates) {
    if (!seen.has(runtimeCandidate.candidateId)) {
      throw new Error(
        `Skill Runtime candidate is missing from Search index: ${runtimeCandidate.candidateId}.`,
      );
    }
  }

  const searchCatalogFingerprint = fingerprintPayload(
    searchCandidates.map((candidate) => ({
      candidateId: candidate.candidateId,
      candidateFingerprint: candidate.candidateFingerprint,
    })),
  );
  const payload = {
    version: SKILL_SEARCH_INDEX_VERSION,
    rankerVersion: SKILL_RUNTIME_RANKER_VERSION,
    semanticDocumentVersion: SKILL_SEMANTIC_DOCUMENT_VERSION,
    directoryFingerprint: runtimeIndex.catalogFingerprint,
    memberIndexFingerprint,
    runtimeIndexFingerprint: runtimeIndex.fingerprint,
    trustIndexFingerprint: trustIndex.fingerprint,
    searchCatalogFingerprint,
    candidates: searchCandidates,
  };
  return {
    ...payload,
    fingerprint: fingerprintPayload(payload),
  };
}

export function buildSkillSearchClientSummary(index) {
  const payload = {
    version: SKILL_SEARCH_INDEX_VERSION,
    rankerVersion: index.rankerVersion,
    semanticDocumentVersion: index.semanticDocumentVersion,
    directoryFingerprint: index.directoryFingerprint,
    memberIndexFingerprint: index.memberIndexFingerprint,
    runtimeIndexFingerprint: index.runtimeIndexFingerprint,
    trustIndexFingerprint: index.trustIndexFingerprint,
    searchCatalogFingerprint: index.searchCatalogFingerprint,
    searchIndexFingerprint: index.fingerprint,
    candidateCount: index.candidates.length,
    runtimeBoundCandidateCount: index.candidates.filter(
      (candidate) => candidate.runtimeCandidateFingerprint,
    ).length,
  };
  return {
    ...payload,
    fingerprint: fingerprintPayload(payload),
  };
}

export function fingerprintSkillSearchIndex(index) {
  return fingerprintPayload({
    version: index.version,
    rankerVersion: index.rankerVersion,
    semanticDocumentVersion: index.semanticDocumentVersion,
    directoryFingerprint: index.directoryFingerprint,
    memberIndexFingerprint: index.memberIndexFingerprint,
    runtimeIndexFingerprint: index.runtimeIndexFingerprint,
    trustIndexFingerprint: index.trustIndexFingerprint,
    searchCatalogFingerprint: index.searchCatalogFingerprint,
    candidates: index.candidates,
  });
}
