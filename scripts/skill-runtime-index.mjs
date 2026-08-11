import { createHash } from "node:crypto";

export const SKILL_RUNTIME_INDEX_VERSION = 2;
export const SKILL_RUNTIME_RANKER_VERSION = "skill-need-local-v3";

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

function installSource(target) {
  return target.targetType === "member"
    ? target.installSource
    : target.project.installSource;
}

function sourceKey(source) {
  return [
    source.repoUrl.trim().toLowerCase().replace(/\.git$/i, ""),
    source.subPath.trim().replace(/^\/+|\/+$/g, ""),
    source.verifiedCommit.trim().toLowerCase(),
  ].join("#");
}

function trustSourceKey(source) {
  return [
    source.repoUrl.trim().toLowerCase().replace(/\.git$/i, ""),
    source.subPath.trim().replace(/^\/+|\/+$/g, ""),
    source.verifiedCommit.trim().toLowerCase(),
  ].join("#");
}

function installMappingKey(source) {
  return [
    source.repoUrl.trim().toLowerCase().replace(/\.git$/i, ""),
    source.subPath.trim().replace(/^\/+|\/+$/g, ""),
  ].join("#");
}

function candidatePayload(target) {
  const source = installSource(target);
  if (!source || !/^[0-9a-f]{40}$/i.test(source.verifiedCommit)) return undefined;
  const parentSkillSets =
    target.targetType === "member"
      ? target.parentSkillSets.map((project) => ({ id: project.id, name: project.name }))
      : [];
  const payload = {
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
    parentSkillSets,
    installSource: {
      repoUrl: source.repoUrl,
      subPath: source.subPath,
      verifiedCommit: source.verifiedCommit.toLowerCase(),
    },
    directoryTreeSha:
      target.targetType === "member" ? target.member.directoryTreeSha : null,
  };
  return {
    ...payload,
    candidateFingerprint: sha256(canonicalJson(payload)),
  };
}

function trustCatalogFingerprint(candidates) {
  return sha256(
    canonicalJson(
      candidates
        .map((candidate) => ({
          candidateId: candidate.candidateId,
          repoUrl: candidate.installSource.repoUrl
            .trim()
            .toLowerCase()
            .replace(/\.git$/i, ""),
          subPath: candidate.installSource.subPath.trim().replace(/^\/+|\/+$/g, ""),
          verifiedCommit: candidate.installSource.verifiedCommit.trim().toLowerCase(),
        }))
        .sort((left, right) => left.candidateId.localeCompare(right.candidateId, "en")),
    ),
  );
}

function validateTrustIndex(trustIndex) {
  if (
    !trustIndex ||
    trustIndex.version !== 1 ||
    !Array.isArray(trustIndex.receipts) ||
    !trustIndex.candidateReceipts ||
    typeof trustIndex.candidateReceipts !== "object" ||
    !/^[0-9a-f]{64}$/.test(trustIndex.catalogFingerprint ?? "") ||
    !/^[0-9a-f]{64}$/.test(trustIndex.fingerprint ?? "")
  ) {
    throw new Error("Skill trust index is unavailable or invalid.");
  }
  const fingerprintPayload = Object.fromEntries(
    Object.entries(trustIndex).filter(([key]) => key !== "fingerprint"),
  );
  if (sha256(canonicalJson(fingerprintPayload)) !== trustIndex.fingerprint) {
    throw new Error("Skill trust index fingerprint does not match its content.");
  }
  const receiptsById = new Map();
  const receiptsBySource = new Map();
  for (const receipt of trustIndex.receipts) {
    const fingerprintPayload = Object.fromEntries(
      Object.entries(receipt).filter(([key]) => key !== "trustFingerprint"),
    );
    if (
      !receipt?.receiptId ||
      !receipt?.source ||
      !/^[0-9a-f]{64}$/.test(receipt.trustFingerprint ?? "") ||
      sha256(canonicalJson(fingerprintPayload)) !== receipt.trustFingerprint ||
      receiptsById.has(receipt.receiptId)
    ) {
      throw new Error("Skill trust index contains an invalid receipt.");
    }
    const key = trustSourceKey(receipt.source);
    if (receiptsBySource.has(key)) {
      throw new Error("Skill trust index contains a duplicate source receipt.");
    }
    receiptsById.set(receipt.receiptId, receipt);
    receiptsBySource.set(key, receipt);
  }
  return { receiptsById, receiptsBySource };
}

export function buildSkillRuntimeIndex({
  candidates,
  memberIndexFingerprint,
  trustIndex,
}) {
  const { receiptsById, receiptsBySource } = validateTrustIndex(trustIndex);
  const readyBeforeDeduplication = candidates
    .filter((candidate) => candidate.installStatus === "ready" && !candidate.deprecated)
    .map(candidatePayload)
    .filter(Boolean);
  const byInstallMapping = new Map();
  const supersededCandidateIds = [];
  for (const candidate of readyBeforeDeduplication) {
    const key = installMappingKey(candidate.installSource);
    const previous = byInstallMapping.get(key);
    if (!previous) {
      byInstallMapping.set(key, candidate);
      continue;
    }
    const preferred =
      Boolean(candidate.directoryTreeSha) !== Boolean(previous.directoryTreeSha)
        ? candidate.directoryTreeSha
          ? candidate
          : previous
        : previous.installSource.verifiedCommit ===
              candidate.installSource.verifiedCommit &&
            previous.targetType !== candidate.targetType
          ? previous.targetType === "project"
            ? previous
            : candidate
          : undefined;
    if (!preferred) {
      throw new Error(
        `Skill runtime index has ambiguous repo/path mapping: ${previous.candidateId} / ${candidate.candidateId}`,
      );
    }
    const superseded = preferred === previous ? candidate : previous;
    supersededCandidateIds.push(superseded.candidateId);
    byInstallMapping.set(key, preferred);
  }
  const readyWithoutTrust = [...byInstallMapping.values()];
  const catalogFingerprint = trustCatalogFingerprint(readyWithoutTrust);
  if (catalogFingerprint !== trustIndex.catalogFingerprint) {
    throw new Error("Skill runtime candidates do not match the Skill trust catalog fingerprint.");
  }
  const ready = readyWithoutTrust.map((candidate) => {
    const receiptId = trustIndex.candidateReceipts[candidate.candidateId];
    const receipt = receiptsById.get(receiptId);
    if (!receipt || receiptsBySource.get(trustSourceKey(candidate.installSource)) !== receipt) {
      throw new Error(`Skill trust receipt is missing for ${candidate.candidateId}.`);
    }
    const trust = {
      receiptId: receipt.receiptId,
      trustFingerprint: receipt.trustFingerprint,
      riskLevel: receipt.riskLevel,
      trustStatus: receipt.trustStatus,
      installPolicy: receipt.installPolicy,
      compatibilityStatus: receipt.compatibilityStatus,
      routerEligible: receipt.routerEligible,
    };
    const payload = Object.fromEntries(
      Object.entries(candidate).filter(([key]) => key !== "candidateFingerprint"),
    );
    const trustedPayload = { ...payload, trust };
    return {
      ...trustedPayload,
      candidateFingerprint: sha256(canonicalJson(trustedPayload)),
    };
  });
  const seenSources = new Map();
  for (const candidate of ready) {
    const key = sourceKey(candidate.installSource);
    const previous = seenSources.get(key);
    if (previous) {
      throw new Error(
        `Skill runtime index contains duplicate install source: ${previous} / ${candidate.candidateId}`,
      );
    }
    seenSources.set(key, candidate.candidateId);
  }

  const nameOrder = new Map(
    [...ready]
      .sort(
        (left, right) =>
          left.name.localeCompare(right.name, "zh-CN") ||
          left.candidateId.localeCompare(right.candidateId, "en"),
      )
      .map((candidate, index) => [candidate.candidateId, index]),
  );
  const ordered = ready
    .map((candidate) => ({
      ...candidate,
      stableNameOrder: nameOrder.get(candidate.candidateId),
    }))
    .sort((left, right) => left.candidateId.localeCompare(right.candidateId, "en"));
  const fingerprintPayload = {
    version: SKILL_RUNTIME_INDEX_VERSION,
    rankerVersion: SKILL_RUNTIME_RANKER_VERSION,
    memberIndexFingerprint,
    catalogFingerprint,
    trustIndexFingerprint: trustIndex.fingerprint,
    supersededCandidateIds: supersededCandidateIds.sort((left, right) =>
      left.localeCompare(right, "en"),
    ),
    candidates: ordered,
  };
  return {
    ...fingerprintPayload,
    fingerprint: sha256(canonicalJson(fingerprintPayload)),
  };
}

export function fingerprintSkillRuntimeIndex(index) {
  return sha256(
    canonicalJson({
      version: index.version,
      rankerVersion: index.rankerVersion,
      memberIndexFingerprint: index.memberIndexFingerprint,
      catalogFingerprint: index.catalogFingerprint,
      trustIndexFingerprint: index.trustIndexFingerprint,
      supersededCandidateIds: index.supersededCandidateIds ?? [],
      candidates: index.candidates,
    }),
  );
}
