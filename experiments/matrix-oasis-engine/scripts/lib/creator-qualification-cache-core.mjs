import { createHash } from "node:crypto";
import {
  validatePrototypeRuntimeEvidenceJson,
  validatePrototypeRuntimeReplayPlanJson,
} from "@matrix-oasis/prototype-runtime-evidence-contracts";
import { validatePrototypeCreatorQualificationJson } from "@matrix-oasis/prototype-creator-qualification-contracts";
import { planPrototypeRuntimeReplay } from "@matrix-oasis/prototype-runtime-evidence";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { loadVerifiedRuntimeEvidenceRun } from "./runtime-evidence-cache-core.mjs";

const SHA_256 = /^sha256:[0-9a-f]{64}$/u;
const HASH_ID = /^[0-9a-f]{64}$/u;
const SOURCE_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/u;
const MEDIA_PATH = /^media\/(?:replay-[0-9]{4}-checkpoint-[0-9]{4}\.png|full-run\.ogv)$/u;
const ASSET_PATH = /^assets\/[A-Za-z0-9._-]+$/u;
const PREVIEW_JSON_FILES = Object.freeze([
  "runtime-game-pack.json",
  "runtime-receipt.json",
  "environment-facts.json",
  "spatial-intent.json",
  "prototype-asset-bundle.json",
  "spatial-solution.json",
  "spatial-verification-report.json",
  "scene-pack.json",
  "spatial-assembly.json",
]);
const IDENTITY_FILES = Object.freeze({
  runtimePackSha256: "runtime-game-pack.json",
  runtimeReceiptSha256: "runtime-receipt.json",
  environmentFactsSha256: "environment-facts.json",
  spatialIntentSha256: "spatial-intent.json",
  assetBundleSha256: "prototype-asset-bundle.json",
  spatialSolutionSha256: "spatial-solution.json",
  spatialVerificationSha256: "spatial-verification-report.json",
});
const FIXED_TOOLCHAIN = Object.freeze({
  godotVersion: "4.6.3",
  renderer: "forward_plus",
  evidenceProfile: "matrix-oasis.runtime-replay/1",
});

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function diagnostic(code) {
  return deepFreeze({
    phase: "qualification",
    severity: "error",
    code,
    path: "",
    message: code,
  });
}

function failure(code = "R16_CREATOR_QUALIFICATION_REFERENCE_INVALID") {
  return deepFreeze({ ok: false, valid: false, diagnostics: [diagnostic(code)] });
}

function sha256(bytes) {
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

function bytes(value) {
  return value instanceof Uint8Array ? value : null;
}

function canonicalJson(text) {
  if (typeof text !== "string") return null;
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch {
    return null;
  }
}

function equalJson(left, right) {
  return canonicalizeJsonValue(left) === canonicalizeJsonValue(right);
}

function validReport(report) {
  return report?.reportVersion === 1 && report.valid === true &&
    Array.isArray(report.diagnostics) && report.diagnostics.length === 0;
}

function referenceMapAdd(references, relative, expectedSha256, expectedLength = null) {
  if (!ASSET_PATH.test(relative) || !SHA_256.test(expectedSha256) ||
      (expectedLength !== null && (!Number.isSafeInteger(expectedLength) || expectedLength < 1))) return false;
  const existing = references.get(relative);
  if (existing && (existing.sha256 !== expectedSha256 ||
      (existing.byteLength !== null && expectedLength !== null && existing.byteLength !== expectedLength))) return false;
  references.set(relative, Object.freeze({
    sha256: expectedSha256,
    byteLength: existing?.byteLength ?? expectedLength,
  }));
  return true;
}

function verifyPreviewFiles(previewFiles) {
  if (!(previewFiles instanceof Map) || previewFiles.size < PREVIEW_JSON_FILES.length + 2 || previewFiles.size > 32) {
    return null;
  }
  const json = Object.create(null);
  for (const relative of PREVIEW_JSON_FILES) {
    const value = bytes(previewFiles.get(relative));
    if (!value) return null;
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(value); } catch { return null; }
    const parsed = canonicalJson(text);
    if (!parsed) return null;
    json[relative] = Object.freeze({ text, value: parsed });
  }

  const references = new Map();
  const assetBundle = json["prototype-asset-bundle.json"].value;
  if (!Array.isArray(assetBundle.materializations)) return null;
  for (const materialization of assetBundle.materializations) {
    if (!Array.isArray(materialization?.assets)) return null;
    if (materialization.source?.type === "builtin-template") continue;
    for (const asset of materialization.assets) {
      if (!referenceMapAdd(references, asset?.path, asset?.sha256, asset?.byteLength)) return null;
    }
  }

  const scenePack = json["scene-pack.json"].value;
  if (!Array.isArray(scenePack.assets)) return null;
  for (const asset of scenePack.assets) {
    const hash = typeof asset?.sha256 === "string" && /^[0-9a-f]{64}$/u.test(asset.sha256)
      ? `sha256:${asset.sha256}` : null;
    if (!referenceMapAdd(references, asset?.path, hash, asset?.byteLength)) return null;
  }

  const assembly = json["spatial-assembly.json"].value;
  const splat = assembly.environment?.splat;
  const collider = assembly.environment?.collider;
  if (!referenceMapAdd(references, splat?.path, splat?.sha256) ||
      !referenceMapAdd(references, collider?.path, collider?.sha256)) return null;

  const actualAssets = [...previewFiles.keys()].filter((relative) => relative.startsWith("assets/")).sort();
  const expectedAssets = [...references.keys()].sort();
  if (actualAssets.length !== expectedAssets.length ||
      actualAssets.some((relative, index) => relative !== expectedAssets[index])) return null;
  for (const [relative, reference] of references) {
    const value = bytes(previewFiles.get(relative));
    if (!value || sha256(value) !== reference.sha256 ||
        (reference.byteLength !== null && value.byteLength !== reference.byteLength)) return null;
  }

  const scenePackSha256 = sha256(previewFiles.get("scene-pack.json"));
  const assemblySha256 = sha256(previewFiles.get("spatial-assembly.json"));
  const solutionSha256 = sha256(previewFiles.get("spatial-solution.json"));
  const solution = json["spatial-solution.json"].value;
  const verification = json["spatial-verification-report.json"].value;
  if (assembly.sources?.scenePackSha256 !== scenePackSha256 ||
      solution.source?.analysisTransformSource?.canonicalSha256 !== assemblySha256 ||
      verification.solutionSha256 !== solutionSha256) return null;

  return Object.freeze({ json, identity: Object.freeze(Object.fromEntries(
    Object.entries(IDENTITY_FILES).map(([field, relative]) => [field, sha256(previewFiles.get(relative))]),
  )) });
}

function verifyMedia(evidence, mediaFiles) {
  if (!(mediaFiles instanceof Map) || mediaFiles.size < 2 || mediaFiles.size > 513) return false;
  const expectedPaths = new Set(["media/full-run.ogv"]);
  let screenshotIndex = 0;
  for (let replayIndex = 0; replayIndex < evidence.observations.length; replayIndex += 1) {
    const observation = evidence.observations[replayIndex];
    for (const checkpoint of observation.checkpoints) {
      const relative = `media/replay-${String(replayIndex).padStart(4, "0")}-checkpoint-${String(checkpoint.sequence).padStart(4, "0")}.png`;
      const screenshot = evidence.media.screenshots[screenshotIndex++];
      const value = bytes(mediaFiles.get(relative));
      if (!screenshot || screenshot.replayId !== observation.replayId ||
          screenshot.locationId !== checkpoint.locationId || !value || sha256(value) !== screenshot.sha256) return false;
      expectedPaths.add(relative);
    }
  }
  if (screenshotIndex < 1 || screenshotIndex !== evidence.media.screenshots.length ||
      mediaFiles.size !== expectedPaths.size) return false;
  for (const [relative, value] of mediaFiles) {
    if (!MEDIA_PATH.test(relative) || !bytes(value) || !expectedPaths.has(relative)) return false;
  }
  const video = bytes(mediaFiles.get("media/full-run.ogv"));
  return video !== null && evidence.media.videos.length === 1 &&
    evidence.media.videos[0].scope === "full-run" && sha256(video) === evidence.media.videos[0].sha256;
}

function replayRequest(preview) {
  const text = (relative) => preview.json[relative].text;
  return Object.freeze({
    assetBundleJson: text("prototype-asset-bundle.json"),
    environmentFactsJson: text("environment-facts.json"),
    runtimeGamePackJson: text("runtime-game-pack.json"),
    runtimeReceiptJson: text("runtime-receipt.json"),
    spatialIntentJson: text("spatial-intent.json"),
    spatialSolutionJson: text("spatial-solution.json"),
    spatialVerificationReportJson: text("spatial-verification-report.json"),
  });
}

function sameBytes(left, right) {
  return left instanceof Uint8Array && right instanceof Uint8Array &&
    left.byteLength === right.byteLength && left.every((value, index) => value === right[index]);
}

function verifySourcePreview(source, evidencePreviewFiles) {
  if (!(source?.previewFiles instanceof Map)) return false;
  const required = [
    "runtime-game-pack.json",
    "runtime-receipt.json",
    "scene-pack.json",
    "spatial-assembly.json",
    "prototype-asset-bundle.json",
    ...[...evidencePreviewFiles.keys()].filter((relative) => relative.startsWith("assets/")),
  ];
  return required.every((relative) => sameBytes(source.previewFiles.get(relative), evidencePreviewFiles.get(relative)));
}

async function inspectReferences(request, operations) {
  if (!request || !SOURCE_RUN_ID.test(request.sourceRunId) || !HASH_ID.test(request.evidenceRunId) ||
      typeof request.evidenceRunRoot !== "string" || typeof request.temporaryRoot !== "string") return null;
  const loadEvidence = operations?.loadEvidence ?? loadVerifiedRuntimeEvidenceRun;
  const loadSource = operations?.loadSource;
  const replan = operations?.replan ?? planPrototypeRuntimeReplay;
  if (typeof loadEvidence !== "function" || typeof loadSource !== "function" || typeof replan !== "function") return null;

  const loadedEvidence = await loadEvidence({
    runRoot: request.evidenceRunRoot,
    temporaryRoot: request.temporaryRoot,
    runId: request.evidenceRunId,
    includeFiles: true,
  });
  const source = await loadSource({
    sourceRunId: request.sourceRunId,
    sourceRunRoot: request.sourceRunRoot,
    temporaryRoot: request.temporaryRoot,
  });
  if (!loadedEvidence || loadedEvidence.runId !== request.evidenceRunId ||
      typeof loadedEvidence.replayPlanJson !== "string" ||
      typeof loadedEvidence.canonicalEvidenceJson !== "string" ||
      !(loadedEvidence.previewFiles instanceof Map) || !(loadedEvidence.mediaFiles instanceof Map) ||
      !source || source.runId !== request.sourceRunId || !SHA_256.test(source.promptSha256) || !MODEL.test(source.model) ||
      !verifySourcePreview(source, loadedEvidence.previewFiles)) return null;

  if (!validReport(validatePrototypeRuntimeReplayPlanJson(loadedEvidence.replayPlanJson)) ||
      !validReport(validatePrototypeRuntimeEvidenceJson(loadedEvidence.canonicalEvidenceJson))) return null;
  const plan = canonicalJson(loadedEvidence.replayPlanJson);
  const evidence = canonicalJson(loadedEvidence.canonicalEvidenceJson);
  const preview = verifyPreviewFiles(loadedEvidence.previewFiles);
  if (!plan || !evidence || !preview || evidence.status !== "passed" ||
      evidence.attempt < 0 || evidence.attempt > 2 ||
      evidence.replayPlanSha256 !== sha256(Buffer.from(loadedEvidence.replayPlanJson, "utf8")) ||
      request.evidenceRunId !== sha256(Buffer.from(loadedEvidence.canonicalEvidenceJson, "utf8")).slice(7) ||
      !equalJson(plan.identity, preview.identity) || !equalJson(evidence.identity, preview.identity) ||
      evidence.observations.length !== plan.replays.length ||
      evidence.observations.some((item, index) => item.outcome !== "passed" ||
        item.replayId !== plan.replays[index]?.id || item.kind !== plan.replays[index]?.kind) ||
      evidence.attempt !== evidence.repairs.length ||
      !verifyMedia(evidence, loadedEvidence.mediaFiles) ||
      evidence.performance.sampleCount !== 300 || evidence.performance.medianFpsMilli < 30_000 ||
      evidence.performance.medianFpsMilli !== Math.floor(1_000_000_000 / evidence.performance.medianFrameMicros)) return null;

  const replanned = await replan(replayRequest(preview));
  if (!replanned?.ok || replanned.canonicalReplayPlanJson !== loadedEvidence.replayPlanJson) return null;

  return Object.freeze({
    source: Object.freeze({ runId: source.runId, promptSha256: source.promptSha256, model: source.model }),
    plan,
    evidence,
    hashes: Object.freeze({
      ...preview.identity,
      replayPlanSha256: sha256(Buffer.from(loadedEvidence.replayPlanJson, "utf8")),
      runtimeEvidenceSha256: sha256(Buffer.from(loadedEvidence.canonicalEvidenceJson, "utf8")),
    }),
    evidenceSummary: Object.freeze({
      runId: request.evidenceRunId,
      attempt: evidence.attempt,
      replayCount: plan.replays.length,
      screenshotCount: evidence.media.screenshots.length,
      videoCount: evidence.media.videos.length,
      sampleCount: evidence.performance.sampleCount,
      medianFrameMicros: evidence.performance.medianFrameMicros,
      medianFpsMilli: evidence.performance.medianFpsMilli,
    }),
  });
}

function inspectionRequest(request, qualification = null) {
  return Object.freeze({
    sourceRunId: qualification?.sourceRunId ?? request?.sourceRunId,
    evidenceRunId: qualification?.evidence?.runId ?? request?.evidenceRunId,
    evidenceRunRoot: request?.evidenceRunRoot,
    sourceRunRoot: request?.sourceRunRoot,
    temporaryRoot: request?.temporaryRoot,
  });
}

export async function buildCreatorQualificationReferences(request, operations = {}) {
  try {
    const inspected = await inspectReferences(inspectionRequest(request), operations);
    if (!inspected) return failure();
    const qualification = {
      format: "matrix-oasis.prototype-creator-qualification",
      formatVersion: "0.1.0",
      canonicalization: "matrix-oasis.canonical-json/1",
      profile: "matrix-oasis.creator-solved-evidence/1",
      status: "qualified",
      promptSha256: inspected.source.promptSha256,
      model: inspected.source.model,
      sourceRunId: inspected.source.runId,
      hashes: inspected.hashes,
      toolchain: { ...FIXED_TOOLCHAIN },
      evidence: inspected.evidenceSummary,
    };
    const canonicalQualificationJson = canonicalizeJsonValue(qualification);
    if (!validReport(validatePrototypeCreatorQualificationJson(canonicalQualificationJson))) return failure();
    return deepFreeze({
      ok: true,
      valid: true,
      qualification: JSON.parse(canonicalQualificationJson),
      canonicalQualificationJson,
      qualificationRunId: sha256(Buffer.from(canonicalQualificationJson, "utf8")).slice(7),
    });
  } catch {
    return failure("R16_CREATOR_QUALIFICATION_REFERENCE_INTERNAL_ERROR");
  }
}

export async function verifyCreatorQualificationReferences(request, operations = {}) {
  try {
    const qualification = request?.qualification;
    if (!qualification || typeof qualification !== "object") return failure();
    const qualificationJson = request.qualificationJson ?? canonicalizeJsonValue(qualification);
    if (!validReport(validatePrototypeCreatorQualificationJson(qualificationJson)) ||
        canonicalizeJsonValue(qualification) !== qualificationJson ||
        (request.qualificationRunId !== undefined &&
          request.qualificationRunId !== sha256(Buffer.from(qualificationJson, "utf8")).slice(7))) return failure();
    const inspected = await inspectReferences(inspectionRequest(request, qualification), operations);
    if (!inspected) return failure();
    const expected = {
      promptSha256: inspected.source.promptSha256,
      model: inspected.source.model,
      sourceRunId: inspected.source.runId,
      hashes: inspected.hashes,
      toolchain: FIXED_TOOLCHAIN,
      evidence: inspected.evidenceSummary,
    };
    const actual = {
      promptSha256: qualification.promptSha256,
      model: qualification.model,
      sourceRunId: qualification.sourceRunId,
      hashes: qualification.hashes,
      toolchain: qualification.toolchain,
      evidence: qualification.evidence,
    };
    if (!equalJson(actual, expected)) return failure();
    return deepFreeze({ ok: true, valid: true, diagnostics: [] });
  } catch {
    return failure("R16_CREATOR_QUALIFICATION_REFERENCE_INTERNAL_ERROR");
  }
}

export function createCreatorQualificationReferenceVerifier(config, operations = {}) {
  const loadSource = config?.loadSource ?? operations?.loadSource;
  const captured = Object.freeze({
    evidenceRunRoot: config?.evidenceRunRoot,
    sourceRunRoot: config?.sourceRunRoot,
    temporaryRoot: config?.temporaryRoot,
  });
  const injected = Object.freeze({ ...operations, loadSource });
  return async (request) => {
    if (typeof loadSource !== "function") return failure();
    return await verifyCreatorQualificationReferences({ ...request, ...captured }, injected);
  };
}
