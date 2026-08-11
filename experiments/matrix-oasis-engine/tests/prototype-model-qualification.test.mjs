import assert from "node:assert/strict";
import {
  lstat,
  mkdtemp,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
} from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  executeQualifyPrototypeModelCli,
  parseQualifyPrototypeArgs,
  PROTOTYPE_QUALIFICATION_PROMPT,
} from "../scripts/lib/prototype-cli-core.mjs";

const TEMP_ROOT = path.resolve(path.parse(process.cwd()).root, "tmp");
let sequence = 0;

function outputPath(label) {
  sequence += 1;
  return path.join(TEMP_ROOT, `matrix-oasis-r8-qualification-${label}-${process.pid}-${sequence}`);
}

function environment() {
  return {
    MATRIX_OASIS_MODEL_ENDPOINT: "https://model.example.invalid/v1/chat/completions",
    MATRIX_OASIS_MODEL_ID: "neutral-model",
    MATRIX_OASIS_MODEL_API_KEY: ["placeholder", "qualification", "value"].join("-"),
  };
}

function artifacts(requestCount = 1) {
  return {
    authoringGamePackJson: '{"artifact":"authoring"}',
    sceneBlueprintJson: '{"artifact":"blueprint"}',
    runtimeGamePackJson: '{"artifact":"runtime"}',
    runtimeReceiptJson: '{"artifact":"receipt"}',
    generationReportJson: JSON.stringify({ requestCount }),
  };
}

function services(overrides = {}) {
  return {
    openFile: open,
    mkdtemp,
    rename,
    rm,
    realpath,
    lstat,
    createOpenAICompatibleProvider: (config) => Object.freeze({ config }),
    generatePrototype: async () => ({ ok: true, artifacts: artifacts() }),
    ...overrides,
  };
}

test("qualification arguments require one new output and explicit upload acknowledgement", () => {
  assert.deepEqual(
    parseQualifyPrototypeArgs(["--output", "candidate", "--acknowledge-external-upload"]),
    { output: "candidate" },
  );
  for (const args of [
    [],
    ["--output", "candidate"],
    ["--unknown"],
    ["--output", "one", "--output", "two", "--acknowledge-external-upload"],
    ["--output", "candidate", "--acknowledge-external-upload", "--acknowledge-external-upload"],
  ]) {
    assert.throws(() => parseQualifyPrototypeArgs(args), /^PrototypeCliOperationalError/);
  }
});

test("fake qualification uses the fixed neutral prompt and atomically publishes five files", async () => {
  const output = outputPath("success");
  let capturedConfig;
  let capturedRequest;
  let capturedProvider;
  try {
    const result = await executeQualifyPrototypeModelCli({
      args: ["--output", output, "--acknowledge-external-upload"],
      tempRoot: TEMP_ROOT,
      environment: environment(),
      ...services({
        createOpenAICompatibleProvider: (config) => {
          capturedConfig = structuredClone(config);
          return Object.freeze({ kind: "fake-provider" });
        },
        generatePrototype: async (request, provider) => {
          capturedRequest = structuredClone(request);
          capturedProvider = provider;
          return { ok: true, artifacts: artifacts(2) };
        },
      }),
    });
    assert.deepEqual(result, {
      exitCode: 0,
      stdout: "PROTOTYPE_MODEL_QUALIFIED requests=2\n",
      stderr: "",
    });
    assert.deepEqual(capturedRequest, { prompt: PROTOTYPE_QUALIFICATION_PROMPT });
    assert.equal(capturedProvider.kind, "fake-provider");
    assert.equal(capturedConfig.endpoint, environment().MATRIX_OASIS_MODEL_ENDPOINT);
    assert.equal(capturedConfig.model, "neutral-model");
    assert.equal(capturedConfig.apiKey, environment().MATRIX_OASIS_MODEL_API_KEY);
    assert.deepEqual((await readdir(output)).sort(), [
      "authoring-game-pack.json",
      "generation-report.json",
      "runtime-game-pack.json",
      "runtime-receipt.json",
      "scene-blueprint.json",
    ]);
    assert.equal(
      (await readFile(path.join(output, "generation-report.json"), "utf8")),
      '{"requestCount":2}',
    );
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("qualification accepts only the exact HTTPS OpenRouter endpoint", async () => {
  const acceptedOutput = outputPath("openrouter");
  const acceptedEnvironment = {
    ...environment(),
    MATRIX_OASIS_MODEL_ENDPOINT: "https://openrouter.ai/api/v1/chat/completions",
    MATRIX_OASIS_MODEL_ID: "openai/gpt-5.6-luna",
  };
  try {
    const accepted = await executeQualifyPrototypeModelCli({
      args: ["--output", acceptedOutput, "--acknowledge-external-upload"],
      tempRoot: TEMP_ROOT,
      environment: acceptedEnvironment,
      ...services(),
    });
    assert.equal(accepted.exitCode, 0);
    for (const endpoint of [
      "https://evil.openrouter.ai/api/v1/chat/completions",
      "https://openrouter.ai.evil.invalid/api/v1/chat/completions",
      "https://openrouter.ai/v1/chat/completions",
      "http://openrouter.ai/api/v1/chat/completions",
      "https://openrouter.ai/api/v1/chat/completions?unsafe=1",
    ]) {
      const rejected = await executeQualifyPrototypeModelCli({
        args: ["--output", outputPath("rejected-openrouter"), "--acknowledge-external-upload"],
        tempRoot: TEMP_ROOT,
        environment: {
          ...acceptedEnvironment,
          MATRIX_OASIS_MODEL_ENDPOINT: endpoint,
        },
        ...services(),
      });
      assert.deepEqual(rejected, {
        exitCode: 2,
        stdout: "",
        stderr: "PROTOTYPE_MODEL_CONFIG_INVALID\n",
      });
    }
  } finally {
    await rm(acceptedOutput, { recursive: true, force: true });
  }
});

test("qualification content rejection writes nothing and exposes only static diagnostics", async () => {
  const output = outputPath("rejected");
  try {
    const result = await executeQualifyPrototypeModelCli({
      args: ["--output", output, "--acknowledge-external-upload"],
      tempRoot: TEMP_ROOT,
      environment: environment(),
      ...services({
        generatePrototype: async () => ({
          ok: false,
          diagnostics: [
            {
              phase: "schema",
              severity: "error",
              code: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED",
              path: "/sceneBlueprint",
              message: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED",
            },
          ],
        }),
      }),
    });
    assert.deepEqual(result, {
      exitCode: 1,
      stdout: "",
      stderr: "PROTOTYPE_PROPOSAL_SCHEMA_REQUIRED /sceneBlueprint\n",
    });
    await assert.rejects(lstat(output), { code: "ENOENT" });
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("qualification is excluded from ordinary verify and the fixed prompt contains no user data", async () => {
  const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(packageJson.scripts.verify.includes("qualify:prototype-model"), false);
  assert.equal(packageJson.scripts["verify:prototype-generation"].includes("test:prototype-qualification"), true);
  assert.equal(new TextEncoder().encode(PROTOTYPE_QUALIFICATION_PROMPT).byteLength < 32_768, true);
  assert.equal(PROTOTYPE_QUALIFICATION_PROMPT.includes("${"), false);
  assert.equal(PROTOTYPE_QUALIFICATION_PROMPT.includes("C:"), false);
});
