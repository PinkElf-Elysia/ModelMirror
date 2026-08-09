import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  MAX_RUNTIME_PACK_BYTES,
  MAX_RUNTIME_RECEIPT_BYTES,
  RuntimePackCliOperationalError,
  executeRuntimePackCli,
  parseRuntimePackCliArgs,
} from "../scripts/lib/runtime-pack-input-core.mjs";

const realModuleRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const temporaryPrefix = "matrix-oasis-runtime-cli-";

class ValidatorOperationalError extends Error {
  constructor(detail = "private runtime validator detail") {
    super(detail);
    this.code = "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR";
  }
}

function validReport() {
  return { reportVersion: 1, valid: true, diagnostics: [] };
}

function invalidReport() {
  return {
    reportVersion: 1,
    valid: false,
    diagnostics: [
      {
        phase: "integrity",
        severity: "error",
        code: "RUNTIME_RECEIPT_ARTIFACT_SHA256_MISMATCH",
        path: "/receipt/artifact/sha256",
        message: "private digest and filesystem detail",
        relatedPath: "/runtimePack",
      },
    ],
  };
}

async function createModule() {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), temporaryPrefix));
  const moduleRoot = path.join(temporaryRoot, "module");
  await fs.mkdir(moduleRoot);
  await fs.writeFile(path.join(moduleRoot, "runtime.json"), '{"runtime":true}', "utf8");
  await fs.writeFile(path.join(moduleRoot, "receipt.json"), '{"receipt":true}', "utf8");
  return { temporaryRoot, moduleRoot };
}

async function removeTemporaryRoot(temporaryRoot) {
  const tempRoot = path.resolve(os.tmpdir());
  const resolved = path.resolve(temporaryRoot);
  assert.equal(path.basename(resolved).startsWith(temporaryPrefix), true);
  assert.equal(resolved.startsWith(`${tempRoot}${path.sep}`), true);
  await fs.rm(resolved, { recursive: true, force: true });
}

function execute(moduleRoot, overrides = {}) {
  return executeRuntimePackCli({
    args: ["runtime.json", "receipt.json"],
    moduleRoot,
    readFile: fs.readFile,
    realpath: fs.realpath,
    stat: fs.stat,
    validateRuntimeGamePackJson: async () => validReport(),
    RuntimeGamePackValidatorOperationalError: ValidatorOperationalError,
    ...overrides,
  });
}

function expectParseFailure(args, code) {
  assert.throws(
    () => parseRuntimePackCliArgs(args),
    (error) =>
      error instanceof RuntimePackCliOperationalError && error.code === code,
  );
}

test("runtime parser accepts exactly two relative JSON inputs and optional JSON mode", () => {
  assert.deepEqual(
    parseRuntimePackCliArgs(["runtime.JSON", "nested/receipt.json", "--json"]),
    {
      runtimeInput: "runtime.JSON",
      receiptInput: "nested/receipt.json",
      json: true,
    },
  );
  assert.deepEqual(
    parseRuntimePackCliArgs(["--json", "nested\\runtime.json", "receipt.json"]),
    {
      runtimeInput: "nested/runtime.json",
      receiptInput: "receipt.json",
      json: true,
    },
  );
});

test("runtime parser rejects arity, options, duplicate JSON, traversal and absolute paths", () => {
  expectParseFailure([], "RUNTIME_PACK_CLI_INPUTS_REQUIRED");
  expectParseFailure(["runtime.json"], "RUNTIME_PACK_CLI_INPUTS_REQUIRED");
  expectParseFailure(
    ["a.json", "b.json", "c.json"],
    "RUNTIME_PACK_CLI_INPUTS_REQUIRED",
  );
  expectParseFailure(
    ["runtime.json", "receipt.json", "--json", "--json"],
    "RUNTIME_PACK_CLI_ARGUMENT_INVALID",
  );
  expectParseFailure(
    ["runtime.json", "receipt.json", "--machine"],
    "RUNTIME_PACK_CLI_UNKNOWN_OPTION",
  );
  expectParseFailure(
    ["../runtime.json", "receipt.json"],
    "RUNTIME_PACK_CLI_PATH_TRAVERSAL",
  );
  const windowsSeparator = String.fromCharCode(92);
  const windowsAbsolute = ["C:", "private", "runtime.json"].join(
    windowsSeparator,
  );
  expectParseFailure(
    [windowsAbsolute, "receipt.json"],
    "RUNTIME_PACK_CLI_PATH_NOT_RELATIVE",
  );
  const posixSeparator = String.fromCharCode(47);
  const posixAbsolute = ["", "private", "runtime.json"].join(posixSeparator);
  expectParseFailure(
    [posixAbsolute, "receipt.json"],
    "RUNTIME_PACK_CLI_PATH_NOT_RELATIVE",
  );
  expectParseFailure(
    ["con.json", "receipt.json"],
    "RUNTIME_PACK_CLI_PATH_INVALID",
  );
});

test("valid runtime pair produces stable exit 0 in human and single-line JSON modes", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const calls = [];
  try {
    const human = await execute(moduleRoot, {
      validateRuntimeGamePackJson: async (...values) => {
        calls.push(values);
        return validReport();
      },
    });
    assert.deepEqual(human, {
      exitCode: 0,
      stdout: "RUNTIME_PACK_VALID\n",
      stderr: "",
      report: validReport(),
    });
    assert.deepEqual(calls, [["{\"runtime\":true}", "{\"receipt\":true}"]]);

    const json = await execute(moduleRoot, {
      args: ["--json", "runtime.json", "receipt.json"],
    });
    assert.equal(json.exitCode, 0);
    assert.equal(json.stderr, "");
    assert.equal(json.stdout.trim().split(/\r?\n/).length, 1);
    assert.deepEqual(JSON.parse(json.stdout), validReport());
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("invalid runtime pair produces exit 1 and reconstructs static diagnostics", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    const human = await execute(moduleRoot, {
      validateRuntimeGamePackJson: async () => invalidReport(),
    });
    assert.equal(human.exitCode, 1);
    assert.equal(human.stdout, "");
    assert.equal(
      human.stderr,
      "RUNTIME_PACK_INVALID integrity RUNTIME_RECEIPT_ARTIFACT_SHA256_MISMATCH /receipt/artifact/sha256\n",
    );
    assert.equal(human.stderr.includes("private"), false);

    const json = await execute(moduleRoot, {
      args: ["runtime.json", "receipt.json", "--json"],
      validateRuntimeGamePackJson: async () => invalidReport(),
    });
    assert.equal(json.exitCode, 1);
    const report = JSON.parse(json.stdout);
    assert.equal(
      report.diagnostics[0].message,
      "RUNTIME_RECEIPT_ARTIFACT_SHA256_MISMATCH",
    );
    assert.equal(json.stdout.includes("private digest"), false);
    assert.deepEqual(report.diagnostics[0].relatedPath, "/runtimePack");
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("16 MiB runtime and 16 KiB receipt boundaries are accepted; larger files are rejected before validation", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  let calls = 0;
  try {
    await fs.writeFile(
      path.join(moduleRoot, "runtime.json"),
      Buffer.alloc(MAX_RUNTIME_PACK_BYTES, 32),
    );
    await fs.writeFile(
      path.join(moduleRoot, "receipt.json"),
      Buffer.alloc(MAX_RUNTIME_RECEIPT_BYTES, 32),
    );
    const exact = await execute(moduleRoot, {
      validateRuntimeGamePackJson: async () => {
        calls += 1;
        return validReport();
      },
    });
    assert.equal(exact.exitCode, 0);
    assert.equal(calls, 1);

    await fs.writeFile(
      path.join(moduleRoot, "runtime.json"),
      Buffer.alloc(MAX_RUNTIME_PACK_BYTES + 1, 32),
    );
    const runtimeLarge = await execute(moduleRoot, {
      args: ["runtime.json", "receipt.json", "--json"],
      validateRuntimeGamePackJson: async () => {
        calls += 1;
        return validReport();
      },
    });
    assert.equal(runtimeLarge.exitCode, 1);
    assert.equal(
      JSON.parse(runtimeLarge.stdout).diagnostics[0].code,
      "RUNTIME_PACK_INPUT_TOO_LARGE",
    );

    await fs.writeFile(path.join(moduleRoot, "runtime.json"), "{}", "utf8");
    await fs.writeFile(
      path.join(moduleRoot, "receipt.json"),
      Buffer.alloc(MAX_RUNTIME_RECEIPT_BYTES + 1, 32),
    );
    const receiptLarge = await execute(moduleRoot, {
      args: ["runtime.json", "receipt.json", "--json"],
    });
    assert.equal(receiptLarge.exitCode, 1);
    assert.equal(
      JSON.parse(receiptLarge.stdout).diagnostics[0].code,
      "RUNTIME_RECEIPT_INPUT_TOO_LARGE",
    );
    assert.equal(calls, 1);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("post-read growth and fatal UTF-8 gates reject content before validator", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  let calls = 0;
  try {
    const sourceRuntime = path.join(moduleRoot, "runtime.json");
    const growth = await execute(moduleRoot, {
      args: ["runtime.json", "receipt.json", "--json"],
      stat: async (candidate) => {
        const actual = await fs.stat(candidate);
        if (path.resolve(candidate) === path.resolve(sourceRuntime)) {
          return {
            size: 1,
            dev: actual.dev,
            ino: actual.ino,
            isFile: () => true,
          };
        }
        return actual;
      },
      readFile: async (candidate) =>
        path.resolve(candidate) === path.resolve(sourceRuntime)
          ? Buffer.alloc(MAX_RUNTIME_PACK_BYTES + 1)
          : fs.readFile(candidate),
      validateRuntimeGamePackJson: async () => {
        calls += 1;
        return validReport();
      },
    });
    assert.equal(growth.exitCode, 1);
    assert.equal(JSON.parse(growth.stdout).diagnostics[0].code, "RUNTIME_PACK_INPUT_TOO_LARGE");

    await fs.writeFile(sourceRuntime, Buffer.from([0xc3, 0x28]));
    const badRuntime = await execute(moduleRoot, {
      args: ["runtime.json", "receipt.json", "--json"],
    });
    assert.equal(
      JSON.parse(badRuntime.stdout).diagnostics[0].code,
      "RUNTIME_PACK_INPUT_UTF8_INVALID",
    );

    await fs.writeFile(sourceRuntime, "{}", "utf8");
    await fs.writeFile(path.join(moduleRoot, "receipt.json"), Buffer.from([0xc3, 0x28]));
    const badReceipt = await execute(moduleRoot, {
      args: ["runtime.json", "receipt.json", "--json"],
    });
    assert.equal(
      JSON.parse(badReceipt.stdout).diagnostics[0].code,
      "RUNTIME_RECEIPT_INPUT_UTF8_INVALID",
    );
    assert.equal(calls, 0);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("external junction and input retarget race are operational and never validated", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const external = path.join(temporaryRoot, "external");
  await fs.mkdir(external);
  await fs.writeFile(path.join(external, "runtime.json"), "{}", "utf8");
  let calls = 0;
  try {
    await fs.symlink(
      external,
      path.join(moduleRoot, "linked"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const linked = await execute(moduleRoot, {
      args: ["linked/runtime.json", "receipt.json"],
      validateRuntimeGamePackJson: async () => {
        calls += 1;
        return validReport();
      },
    });
    assert.equal(linked.exitCode, 2);
    assert.equal(linked.stderr, "RUNTIME_PACK_CLI_PATH_OUTSIDE_MODULE\n");

    const runtimePath = path.join(moduleRoot, "runtime.json");
    let runtimeRealpathCalls = 0;
    const raced = await execute(moduleRoot, {
      realpath: async (candidate) => {
        const resolved = await fs.realpath(candidate);
        if (path.resolve(candidate) === path.resolve(runtimePath)) {
          runtimeRealpathCalls += 1;
          if (runtimeRealpathCalls > 1) {
            return path.join(external, "runtime.json");
          }
        }
        return resolved;
      },
      validateRuntimeGamePackJson: async () => {
        calls += 1;
        return validReport();
      },
    });
    assert.equal(raced.exitCode, 2);
    assert.equal(raced.stderr, "RUNTIME_PACK_CLI_IO_ERROR\n");
    assert.equal(calls, 0);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("validator operational errors, throws and malformed reports use static exit 2", async () => {
  const sentinel = "private-path-secret-value";
  for (const validateRuntimeGamePackJson of [
    async () => {
      throw new Error(sentinel);
    },
    async () => {
      const error = new Error(sentinel);
      error.code = "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR";
      throw error;
    },
    async () => null,
    async () => ({ reportVersion: 1, valid: true, diagnostics: [], [sentinel]: sentinel }),
    async () => ({
      reportVersion: 1,
      valid: false,
      diagnostics: [
        {
          phase: "schema",
          severity: "error",
          code: sentinel,
          path: "/runtimePack",
          message: sentinel,
        },
      ],
    }),
  ]) {
    const { temporaryRoot, moduleRoot } = await createModule();
    try {
      const result = await execute(moduleRoot, { validateRuntimeGamePackJson });
      assert.equal(result.exitCode, 2);
      assert.equal(result.stdout.includes(sentinel), false);
      assert.equal(result.stderr.includes(sentinel), false);
      assert.equal(result.stderr, "RUNTIME_PACK_CLI_INTERNAL_ERROR\n");
    } finally {
      await removeTemporaryRoot(temporaryRoot);
    }
  }

  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    const declared = await execute(moduleRoot, {
      validateRuntimeGamePackJson: async () => {
        throw new ValidatorOperationalError(sentinel);
      },
    });
    assert.deepEqual(declared, {
      exitCode: 2,
      stdout: "",
      stderr: "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR\n",
    });
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("filesystem and non-file failures never echo paths or exception values", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const sentinel = "private-system-path";
  try {
    const io = await execute(moduleRoot, {
      realpath: async () => {
        throw new Error(sentinel);
      },
    });
    assert.equal(io.stderr, "RUNTIME_PACK_CLI_IO_ERROR\n");
    assert.equal(io.stderr.includes(sentinel), false);

    await fs.rm(path.join(moduleRoot, "runtime.json"));
    await fs.mkdir(path.join(moduleRoot, "runtime.json"));
    const directory = await execute(moduleRoot);
    assert.equal(directory.stderr, "RUNTIME_PACK_CLI_INPUT_NOT_FILE\n");
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("real runtime CLI resolves workspace validator and emits one invalid JSON line", () => {
  const result = spawnSync(
    process.execPath,
    [
      "scripts/validate-runtime-pack.mjs",
      "package.json",
      "package.json",
      "--json",
    ],
    {
      cwd: realModuleRoot,
      encoding: "utf8",
      shell: false,
      windowsHide: true,
    },
  );
  assert.equal(result.error, undefined);
  assert.equal(result.status, 1);
  assert.equal(result.stderr, "");
  assert.equal(result.stdout.trim().split(/\r?\n/).length, 1);
  const report = JSON.parse(result.stdout);
  assert.equal(report.valid, false);
  assert.equal(report.reportVersion, 1);
});
