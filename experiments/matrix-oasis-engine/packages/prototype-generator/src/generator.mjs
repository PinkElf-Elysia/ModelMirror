import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import {
  prepareGenerationProposalJson,
} from "@matrix-oasis/prototype-generation-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import { PrototypeGeneratorOperationalError } from "./openai-compatible.mjs";
import {
  evaluateAcceptanceProfile,
  normalizeAcceptanceOptions,
} from "./acceptance-profile.mjs";

const PROMPT_MAX_BYTES = 32_768;
const RESPONSE_MAX_BYTES = 1_048_576;
const MAX_REQUESTS = 3;
const ARTIFACT_NAMES = Object.freeze([
  "authoring-game-pack.json",
  "scene-blueprint.json",
  "runtime-game-pack.json",
  "runtime-receipt.json",
]);

function fail() {
  throw new PrototypeGeneratorOperationalError();
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

function descriptorsOf(value) {
  try {
    if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
      fail();
    }
    return Object.getOwnPropertyDescriptors(value);
  } catch (error) {
    if (error instanceof PrototypeGeneratorOperationalError) {
      throw error;
    }
    fail();
  }
}

function exactRecord(value, expectedKeys) {
  const descriptors = descriptorsOf(value);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.length !== expectedKeys.length ||
    keys.some(
      (key) =>
        typeof key !== "string" ||
        !expectedKeys.includes(key) ||
        !descriptors[key].enumerable ||
        !("value" in descriptors[key]),
    )
  ) {
    fail();
  }
  return Object.fromEntries(
    expectedKeys.map((key) => [key, descriptors[key].value]),
  );
}

function wellFormed(value) {
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

function staticDiagnostic(code, path) {
  return deepFreeze({
    phase: "semantic",
    severity: "error",
    code,
    path,
    message: code,
  });
}

function rejected(code, path) {
  return deepFreeze({ ok: false, diagnostics: [staticDiagnostic(code, path)] });
}

function validateRequest(value) {
  let request;
  try {
    request = exactRecord(value, ["prompt"]);
  } catch {
    return { ok: false, result: rejected("PROTOTYPE_REQUEST_INVALID", "") };
  }
  if (
    typeof request.prompt !== "string" ||
    request.prompt.trim().length === 0 ||
    !wellFormed(request.prompt) ||
    new TextEncoder().encode(request.prompt).byteLength > PROMPT_MAX_BYTES
  ) {
    return {
      ok: false,
      result: rejected("PROTOTYPE_REQUEST_TEXT_INVALID", "/prompt"),
    };
  }
  return { ok: true, prompt: request.prompt };
}

function validateProvider(value) {
  const provider = exactRecord(value, ["kind", "model", "requestProposal"]);
  if (
    typeof provider.kind !== "string" ||
    typeof provider.model !== "string" ||
    provider.model.length < 1 ||
    provider.model.length > 256 ||
    !wellFormed(provider.model) ||
    typeof provider.requestProposal !== "function"
  ) {
    fail();
  }
  return provider;
}

function normalizeUsage(value) {
  if (value === null) {
    return null;
  }
  const usage = exactRecord(value, ["promptTokens", "completionTokens", "totalTokens"]);
  for (const key of ["promptTokens", "completionTokens", "totalTokens"]) {
    if (!Number.isSafeInteger(usage[key]) || usage[key] < 0) {
      fail();
    }
  }
  return usage;
}

function normalizeProviderResponse(value, expectedModel) {
  const response = exactRecord(value, ["candidateText", "model", "usage"]);
  if (
    typeof response.candidateText !== "string" ||
    new TextEncoder().encode(response.candidateText).byteLength > RESPONSE_MAX_BYTES ||
    response.model !== expectedModel
  ) {
    fail();
  }
  return {
    candidateText: response.candidateText,
    usage: normalizeUsage(response.usage),
  };
}

function normalizeCompileSuccess(value) {
  const result = exactRecord(value, ["ok", "runtimePack", "canonicalJson", "receipt"]);
  if (
    result.ok !== true ||
    typeof result.canonicalJson !== "string" ||
    !result.runtimePack ||
    typeof result.runtimePack !== "object" ||
    !result.receipt ||
    typeof result.receipt !== "object"
  ) {
    fail();
  }
  return result;
}

function normalizePreparedRuntime(value) {
  const result = exactRecord(value, ["ok", "prepared"]);
  if (result.ok !== true || !result.prepared || typeof result.prepared !== "object") {
    fail();
  }
  return result.prepared;
}

function normalizeCreatedSession(value) {
  const result = exactRecord(value, ["ok", "snapshot", "inspection", "emittedCues"]);
  if (
    result.ok !== true ||
    !result.inspection ||
    typeof result.inspection !== "object" ||
    !Array.isArray(result.inspection.actions)
  ) {
    fail();
  }
  return result;
}

function addUsage(total, usage) {
  if (usage === null) {
    total.complete = false;
    return;
  }
  for (const [targetKey, sourceKey] of [
    ["promptTokens", "promptTokens"],
    ["completionTokens", "completionTokens"],
    ["totalTokens", "totalTokens"],
  ]) {
    const next = total[targetKey] + usage[sourceKey];
    if (!Number.isSafeInteger(next)) {
      fail();
    }
    total[targetKey] = next;
  }
}

async function sha256(text, digest) {
  const bytes = new TextEncoder().encode(text);
  let result;
  try {
    result = await digest("SHA-256", bytes);
  } catch {
    fail();
  }
  if (!(result instanceof ArrayBuffer) || result.byteLength !== 32) {
    fail();
  }
  return {
    sha256: `sha256:${[...new Uint8Array(result)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("")}`,
    byteLength: bytes.byteLength,
  };
}

function safeRepairDiagnostics(report) {
  return report.diagnostics.map((item) =>
    Object.freeze({ code: item.code, path: item.path }),
  );
}

export async function generatePrototypeWithServices(
  requestValue,
  providerValue,
  services,
  optionsValue,
) {
  try {
    const request = validateRequest(requestValue);
    if (!request.ok) {
      return request.result;
    }
    const options = normalizeAcceptanceOptions(optionsValue);
    if (!options.ok) {
      return rejected("PROTOTYPE_ACCEPTANCE_PROFILE_INVALID", "/acceptanceProfile");
    }
    const provider = validateProvider(providerValue);
    const service = exactRecord(services, [
      "compileAuthoringGamePackJson",
      "canonicalizeJsonValue",
      "prepareRuntimeGamePackJson",
      "createRuntimeGameSession",
      "digest",
    ]);
    if (
      typeof service.compileAuthoringGamePackJson !== "function" ||
      typeof service.canonicalizeJsonValue !== "function" ||
      typeof service.prepareRuntimeGamePackJson !== "function" ||
      typeof service.createRuntimeGameSession !== "function" ||
      typeof service.digest !== "function"
    ) {
      fail();
    }

    const usage = {
      complete: true,
      promptTokens: 0,
      completionTokens: 0,
      totalTokens: 0,
    };
    let requestCount = 0;
    let candidateText;
    let preparedProposal;
    let latestReport;
    while (requestCount < MAX_REQUESTS) {
      const providerRequest =
        requestCount === 0
          ? {
              kind: "initial",
              prompt: request.prompt,
              ...(options.profile === null
                ? {}
                : { acceptanceProfile: options.profile }),
            }
          : {
              kind: "repair",
              previousCandidate: candidateText,
              diagnostics: safeRepairDiagnostics(latestReport),
              ...(options.profile === null
                ? {}
                : { acceptanceProfile: options.profile }),
            };
      let rawResponse;
      try {
        rawResponse = await provider.requestProposal(providerRequest);
      } catch {
        fail();
      }
      const response = normalizeProviderResponse(rawResponse, provider.model);
      requestCount += 1;
      candidateText = response.candidateText;
      addUsage(usage, response.usage);
      preparedProposal = prepareGenerationProposalJson(candidateText);
      if (preparedProposal.ok) {
        const acceptanceDiagnostics = evaluateAcceptanceProfile(
          preparedProposal,
          options.profile,
        );
        if (acceptanceDiagnostics.length === 0) {
          break;
        }
        latestReport = { diagnostics: acceptanceDiagnostics };
      } else {
        latestReport = preparedProposal.validationReport;
      }
    }
    const finalAcceptanceDiagnostics = preparedProposal?.ok
      ? evaluateAcceptanceProfile(preparedProposal, options.profile)
      : Object.freeze([]);
    if (!preparedProposal?.ok || finalAcceptanceDiagnostics.length > 0) {
      return deepFreeze({
        ok: false,
        diagnostics: preparedProposal?.ok
          ? finalAcceptanceDiagnostics
          : latestReport.diagnostics,
      });
    }

    let compiled;
    try {
      compiled = normalizeCompileSuccess(
        await service.compileAuthoringGamePackJson(
          preparedProposal.canonicalAuthoringJson,
        ),
      );
    } catch {
      fail();
    }
    let canonicalReceiptJson;
    try {
      canonicalReceiptJson = service.canonicalizeJsonValue(compiled.receipt);
    } catch {
      fail();
    }
    let runtimePrepared;
    try {
      runtimePrepared = normalizePreparedRuntime(
        await service.prepareRuntimeGamePackJson(
          compiled.canonicalJson,
          canonicalReceiptJson,
        ),
      );
    } catch {
      fail();
    }
    let initialSession;
    try {
      initialSession = normalizeCreatedSession(
        service.createRuntimeGameSession(runtimePrepared),
      );
    } catch {
      fail();
    }
    const declaredActionCount = preparedProposal.value.authoringGamePack.nodes.reduce(
      (count, node) => count + node.actions.length,
      0,
    );
    if (declaredActionCount < 1 || initialSession.inspection.status !== "active") {
      fail();
    }

    const artifactTexts = [
      preparedProposal.canonicalAuthoringJson,
      preparedProposal.canonicalSceneBlueprintJson,
      compiled.canonicalJson,
      canonicalReceiptJson,
    ];
    const artifactEvidence = [];
    for (let index = 0; index < ARTIFACT_NAMES.length; index += 1) {
      artifactEvidence.push(
        Object.freeze({
          name: ARTIFACT_NAMES[index],
          ...(await sha256(artifactTexts[index], service.digest)),
        }),
      );
    }
    const generationReport = deepFreeze({
      format: "matrix-oasis.prototype-generation-report",
      formatVersion: "0.1.0",
      model: provider.model,
      requestCount,
      usage: usage.complete
        ? {
            promptTokens: usage.promptTokens,
            completionTokens: usage.completionTokens,
            totalTokens: usage.totalTokens,
          }
        : null,
      artifacts: artifactEvidence,
      runtimeCheck: {
        status: "ready",
        declaredActionCount,
        initialAvailableActionCount: initialSession.inspection.actions.filter(
          (action) => action.available === true,
        ).length,
      },
    });
    let generationReportJson;
    try {
      generationReportJson = service.canonicalizeJsonValue(generationReport);
    } catch {
      fail();
    }
    return deepFreeze({
      ok: true,
      artifacts: {
        authoringGamePackJson: preparedProposal.canonicalAuthoringJson,
        sceneBlueprintJson: preparedProposal.canonicalSceneBlueprintJson,
        runtimeGamePackJson: compiled.canonicalJson,
        runtimeReceiptJson: canonicalReceiptJson,
        generationReportJson,
      },
    });
  } catch (error) {
    if (error instanceof PrototypeGeneratorOperationalError) {
      throw error;
    }
    fail();
  }
}

export function generatePrototype(request, provider, options) {
  return generatePrototypeWithServices(request, provider, {
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    digest: (algorithm, bytes) => globalThis.crypto.subtle.digest(algorithm, bytes),
  }, options);
}
