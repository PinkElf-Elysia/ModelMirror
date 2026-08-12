import assert from "node:assert/strict";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  MESHY_QUALIFICATION_OPERATIONS,
  executeQualifyMeshyAssetCli,
  parseQualifyMeshyAssetArgs,
} from "../scripts/lib/prototype-asset-cli-core.mjs";

const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
const services = { lstat, mkdir, openFile: open, realpath };
const secretTask = ["remote", "task", "not", "for", "logs"].join("-");
const secretUrl = `http://127.0.0.1:1/${["signed", "asset", "url"].join("-")}`;

async function fixture() {
  const prototypeDir = await mkdtemp(path.join(tempRoot, "matrix-oasis-r9-qualification-input-"));
  const qualificationRoot = path.join(tempRoot, `${path.basename(prototypeDir).toLowerCase()}-state`);
  for (const name of [
    "authoring-game-pack.json",
    "scene-blueprint.json",
    "runtime-game-pack.json",
    "runtime-receipt.json",
  ]) await writeFile(path.join(prototypeDir, name), "{}");
  return { prototypeDir, qualificationRoot };
}

async function cleanup(value) {
  await rm(value.qualificationRoot, { recursive: true, force: true });
  await rm(value.prototypeDir, { recursive: true, force: true });
}

function planPrototypeAssets() {
  return Promise.resolve(Object.freeze({
    ok: true,
    plan: Object.freeze({
      blueprint: Object.freeze({
        assetBriefs: Object.freeze([
          Object.freeze({ id: "asset-prop", kind: "prop", prompt: "Neutral object", roles: Object.freeze(["visual", "collider"]) }),
        ]),
      }),
    }),
  }));
}

function operationArgs(prototypeDir, operation) {
  return ["--prototype-dir", prototypeDir, "--brief", "asset-prop", "--operation", operation];
}

function fakeProvider() {
  const calls = [];
  const polls = new Map();
  return {
    calls,
    provider: Object.freeze({
      async createPreview({ prompt }) {
        calls.push(["preview-create", prompt]);
        return { ok: true, taskId: `${secretTask}-preview` };
      },
      async createRefine({ previewTaskId }) {
        calls.push(["refine-create", previewTaskId]);
        return { ok: true, taskId: `${secretTask}-refine` };
      },
      async getTask({ taskId }) {
        calls.push(["poll", taskId]);
        const count = (polls.get(taskId) ?? 0) + 1;
        polls.set(taskId, count);
        return count === 1
          ? { ok: true, task: { status: "pending", progress: 50, glbUrl: null, consumedCredits: null } }
          : { ok: true, task: { status: "succeeded", progress: 100, glbUrl: secretUrl, consumedCredits: 15 } };
      },
      async downloadGlb({ url }) {
        calls.push(["download", url]);
        return { ok: true, bytes: new Uint8Array([0x67, 0x6c, 0x54, 0x46]) };
      },
    }),
  };
}

async function execute(value, operation, provider) {
  return executeQualifyMeshyAssetCli({
    args: operationArgs(value.prototypeDir, operation),
    tempRoot,
    qualificationRoot: value.qualificationRoot,
    services,
    provider,
    planPrototypeAssets,
    pollAttempts: 3,
    pollIntervalMs: 0,
    delay: async () => {},
  });
}

test("qualification parser exposes exactly six separately approved stages", () => {
  assert.deepEqual(MESHY_QUALIFICATION_OPERATIONS, [
    "preview-create", "preview-poll", "preview-download",
    "refine-create", "refine-poll", "refine-download",
  ]);
  assert.equal(
    parseQualifyMeshyAssetArgs(operationArgs("x", "preview-create")).operation,
    "preview-create",
  );
  for (const args of [[], operationArgs("x", "all"), [...operationArgs("x", "preview-create"), "--extra", "x"]]) {
    assert.throws(() => parseQualifyMeshyAssetArgs(args));
  }
});

test("qualification runs each remote phase independently and never prints remote identifiers", async () => {
  const value = await fixture();
  const fake = fakeProvider();
  try {
    for (const operation of MESHY_QUALIFICATION_OPERATIONS) {
      const stage = await execute(value, operation, fake.provider);
      assert.equal(stage.exitCode, 0, stage.stderr);
      assert.equal(stage.stdout.includes(secretTask), false);
      assert.equal(stage.stdout.includes(secretUrl), false);
      assert.match(stage.stdout, new RegExp(`operation=${operation}`));
    }
    assert.deepEqual(fake.calls.map(([operation]) => operation), [
      "preview-create", "poll", "poll", "download",
      "refine-create", "poll", "poll", "download",
    ]);
    assert.deepEqual(
      [...await readFile(path.join(value.qualificationRoot, "acquired", "asset-prop.glb"))],
      [0x67, 0x6c, 0x54, 0x46],
    );
  } finally {
    await cleanup(value);
  }
});

test("qualification rejects out-of-order stages and duplicate stage execution", async () => {
  const value = await fixture();
  const fake = fakeProvider();
  try {
    const outOfOrder = await execute(value, "preview-poll", fake.provider);
    assert.equal(outOfOrder.exitCode, 2);
    const created = await execute(value, "preview-create", fake.provider);
    assert.equal(created.exitCode, 0);
    const duplicate = await execute(value, "preview-create", fake.provider);
    assert.equal(duplicate.exitCode, 2);
    assert.equal(duplicate.stderr.includes(secretTask), false);
    assert.equal(fake.calls.filter(([operation]) => operation === "preview-create").length, 1);
  } finally {
    await cleanup(value);
  }
});

test("qualification provider rejection remains static and writes no successful stage", async () => {
  const value = await fixture();
  try {
    const rejected = await execute(value, "preview-create", {
      async createPreview() { return { ok: false, diagnostics: [] }; },
    });
    assert.deepEqual(rejected, {
      exitCode: 1,
      stdout: "",
      stderr: "MESHY_QUALIFICATION_PROVIDER_REJECTED\n",
    });
    await assert.rejects(readFile(path.join(value.qualificationRoot, "asset-prop", "preview-created.json")));
  } finally {
    await cleanup(value);
  }
});

test("qualification entrypoint reads only its dedicated key and fixes the external state root", async () => {
  const source = await readFile(new URL("../scripts/qualify-meshy-asset.mjs", import.meta.url), "utf8");
  assert.match(source, /MATRIX_OASIS_MESHY_API_KEY/u);
  for (const forbidden of ["LLM_GATEWAY", "OPENROUTER", "MARBLE", "MESHY_ENDPOINT", "MESHY_MODEL"]) {
    assert.equal(source.includes(forbidden), false);
  }
  assert.match(source, /matrix-oasis-r9-qualification-meshy-20260811/u);
});
