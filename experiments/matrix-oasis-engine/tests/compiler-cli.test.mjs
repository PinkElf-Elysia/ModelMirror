import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  GamePackCompilerOperationalError as PublicCompilerOperationalError,
  compileAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-compiler";
import {
  RuntimeGamePackValidatorOperationalError as PublicValidatorOperationalError,
  validateRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-validator";
import {
  MAX_AUTHORING_PACK_BYTES,
  RUNTIME_PACK_FILE_NAME,
  RUNTIME_PACK_RECEIPT_FILE_NAME,
  RuntimePackCliOperationalError,
  executeCompilePackCli,
  parseCompilePackCliArgs,
} from "../scripts/lib/runtime-pack-input-core.mjs";

const realModuleRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const temporaryPrefix = "matrix-oasis-compiler-cli-";

class CompilerOperationalError extends Error {
  constructor(detail = "private compiler detail") {
    super(detail);
    this.code = "PACK_COMPILER_INTERNAL_ERROR";
  }
}

class ValidatorOperationalError extends Error {
  constructor(detail = "private validator detail") {
    super(detail);
    this.code = "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR";
  }
}

function validReport() {
  return { reportVersion: 1, valid: true, diagnostics: [] };
}

function invalidAuthoringReport() {
  return {
    reportVersion: 1,
    valid: false,
    diagnostics: [
      {
        phase: "schema",
        severity: "error",
        code: "PACK_SCHEMA_REQUIRED",
        path: "/title",
        message: "private source detail",
      },
    ],
  };
}

function compiledResult(sentinel = "not-published-in-cli-json") {
  const runtimePack = { marker: sentinel };
  const canonicalJson = canonicalizeJsonValue(runtimePack);
  return {
    ok: true,
    runtimePack,
    canonicalJson,
    receipt: {
      format: "matrix-oasis.runtime-game-pack-receipt",
      formatVersion: "0.1.0",
      canonicalization: "matrix-oasis.canonical-json/1",
      compiler: {
        id: "@matrix-oasis/game-pack-compiler",
        version: "0.1.0-r3",
      },
      artifact: {
        format: "matrix-oasis.runtime-game-pack",
        formatVersion: "0.1.0",
        sha256: "0".repeat(64),
        byteLength: new TextEncoder().encode(canonicalJson).byteLength,
      },
    },
  };
}

async function createModule() {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), temporaryPrefix));
  const moduleRoot = path.join(temporaryRoot, "module");
  await fs.mkdir(moduleRoot);
  await fs.writeFile(path.join(moduleRoot, "source.json"), "{}", "utf8");
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
  return executeCompilePackCli({
    args: ["source.json", "--output", "fixture"],
    moduleRoot,
    readFile: fs.readFile,
    openFile: fs.open,
    mkdir: fs.mkdir,
    mkdtemp: fs.mkdtemp,
    rename: fs.rename,
    rm: fs.rm,
    realpath: fs.realpath,
    stat: fs.stat,
    lstat: fs.lstat,
    compileAuthoringGamePackJson: async () => compiledResult(),
    GamePackCompilerOperationalError: CompilerOperationalError,
    canonicalizeJsonValue,
    validateRuntimeGamePackJson: async () => validReport(),
    RuntimeGamePackValidatorOperationalError: ValidatorOperationalError,
    ...overrides,
  });
}

function fileHandleFacade(handle, overrides = {}) {
  return {
    stat: (...args) => handle.stat(...args),
    writeFile: (...args) => handle.writeFile(...args),
    sync: (...args) => handle.sync(...args),
    read: (...args) => handle.read(...args),
    close: (...args) => handle.close(...args),
    ...overrides,
  };
}

function expectParseFailure(args, code) {
  assert.throws(
    () => parseCompilePackCliArgs(args),
    (error) =>
      error instanceof RuntimePackCliOperationalError && error.code === code,
  );
}

test("compile parser accepts fixed grammar and safe lowercase slug", () => {
  assert.deepEqual(
    parseCompilePackCliArgs([
      "fixtures/source.JSON",
      "--output",
      "sample-01",
      "--json",
    ]),
    { input: "fixtures/source.JSON", output: "sample-01", json: true },
  );
  assert.deepEqual(
    parseCompilePackCliArgs([
      "--json",
      "--output",
      "a",
      "fixtures\\source.json",
    ]),
    { input: "fixtures/source.json", output: "a", json: true },
  );
});

test("compile parser rejects missing, duplicate, unknown and traversal arguments", () => {
  expectParseFailure([], "PACK_COMPILE_CLI_INPUT_REQUIRED");
  expectParseFailure(["source.json"], "PACK_COMPILE_CLI_OUTPUT_REQUIRED");
  expectParseFailure(
    ["source.json", "--output"],
    "PACK_COMPILE_CLI_OUTPUT_INVALID",
  );
  expectParseFailure(
    ["source.json", "second.json", "--output", "safe"],
    "PACK_COMPILE_CLI_MULTIPLE_INPUTS",
  );
  expectParseFailure(
    ["source.json", "--output", "safe", "--json", "--json"],
    "PACK_COMPILE_CLI_ARGUMENT_INVALID",
  );
  expectParseFailure(
    ["source.json", "--output", "safe", "--machine"],
    "PACK_COMPILE_CLI_UNKNOWN_OPTION",
  );
  expectParseFailure(
    ["../source.json", "--output", "safe"],
    "PACK_COMPILE_CLI_PATH_TRAVERSAL",
  );
  const windowsSeparator = String.fromCharCode(92);
  const windowsAbsolute = ["C:", "private", "source.json"].join(
    windowsSeparator,
  );
  expectParseFailure(
    [windowsAbsolute, "--output", "safe"],
    "PACK_COMPILE_CLI_PATH_NOT_RELATIVE",
  );
  const posixSeparator = String.fromCharCode(47);
  const posixAbsolute = ["", "private", "source.json"].join(posixSeparator);
  expectParseFailure(
    [posixAbsolute, "--output", "safe"],
    "PACK_COMPILE_CLI_PATH_NOT_RELATIVE",
  );
  for (const input of ["con.json", "folder/aux.data.json", "bad:name.json"]) {
    expectParseFailure(
      [input, "--output", "safe"],
      "PACK_COMPILE_CLI_PATH_INVALID",
    );
  }
});

test("compile parser rejects unsafe and Windows device output names", () => {
  for (const slug of [
    "Upper",
    "two_words",
    "-leading",
    "trailing-",
    "a".repeat(65),
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com9",
    "lpt1",
    "lpt9",
  ]) {
    expectParseFailure(
      ["source.json", "--output", slug],
      "PACK_COMPILE_CLI_OUTPUT_INVALID",
    );
  }
});

test("successful publication uses exclusive read-write handles and one atomic directory rename", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const opens = [];
  try {
    const result = await execute(moduleRoot, {
      args: ["source.json", "--output", "atomic-pair", "--json"],
      openFile: async (file, flags) => {
        opens.push({ file, flags });
        return fs.open(file, flags);
      },
    });
    assert.equal(result.exitCode, 0);
    assert.equal(result.stderr, "");
    assert.equal(result.stdout.trim().split(/\r?\n/).length, 1);
    const published = JSON.parse(result.stdout);
    assert.deepEqual(Object.keys(published), [
      "resultVersion",
      "ok",
      "files",
      "receipt",
    ]);
    assert.equal(result.stdout.includes("not-published-in-cli-json"), false);
    assert.deepEqual(opens.map(({ flags }) => flags), ["wx+", "wx+", "r", "r"]);
    assert.deepEqual(
      opens.map(({ file }) => path.basename(file)),
      [
        RUNTIME_PACK_FILE_NAME,
        RUNTIME_PACK_RECEIPT_FILE_NAME,
        RUNTIME_PACK_FILE_NAME,
        RUNTIME_PACK_RECEIPT_FILE_NAME,
      ],
    );

    const outputRoot = path.join(moduleRoot, "exports", "atomic-pair");
    const names = (await fs.readdir(outputRoot)).sort();
    assert.deepEqual(names, [
      RUNTIME_PACK_RECEIPT_FILE_NAME,
      RUNTIME_PACK_FILE_NAME,
    ]);
    for (const name of names) {
      const bytes = await fs.readFile(path.join(outputRoot, name));
      assert.notEqual(bytes[0], 0xef);
      assert.notEqual(bytes.at(-1), 0x0a);
      assert.notEqual(bytes.at(-1), 0x0d);
    }
    const leftovers = (await fs.readdir(path.join(moduleRoot, "exports"))).filter(
      (name) => name.startsWith(".matrix-oasis-"),
    );
    assert.deepEqual(leftovers, []);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("invalid content never creates exports and rebuilds static diagnostics", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    const result = await execute(moduleRoot, {
      args: ["source.json", "--output", "invalid", "--json"],
      compileAuthoringGamePackJson: async () => ({
        ok: false,
        validationReport: invalidAuthoringReport(),
      }),
    });
    assert.equal(result.exitCode, 1);
    assert.equal(result.stderr, "");
    const report = JSON.parse(result.stdout);
    assert.equal(report.diagnostics[0].message, "PACK_SCHEMA_REQUIRED");
    assert.equal(result.stdout.includes("private source detail"), false);
    await assert.rejects(fs.access(path.join(moduleRoot, "exports")));
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("one MiB is accepted; stat and post-read growth gates are content-invalid", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    const source = path.join(moduleRoot, "source.json");
    await fs.writeFile(source, Buffer.alloc(MAX_AUTHORING_PACK_BYTES, 32));
    let called = 0;
    const exact = await execute(moduleRoot, {
      compileAuthoringGamePackJson: async () => {
        called += 1;
        return { ok: false, validationReport: invalidAuthoringReport() };
      },
    });
    assert.equal(exact.exitCode, 1);
    assert.equal(called, 1);

    await fs.writeFile(source, Buffer.alloc(MAX_AUTHORING_PACK_BYTES + 1, 32));
    const tooLarge = await execute(moduleRoot, {
      args: ["source.json", "--output", "too-large", "--json"],
    });
    assert.equal(tooLarge.exitCode, 1);
    assert.equal(JSON.parse(tooLarge.stdout).diagnostics[0].code, "PACK_INPUT_TOO_LARGE");

    const growth = await execute(moduleRoot, {
      args: ["source.json", "--output", "grew", "--json"],
      stat: async (file) => ({
        size: 1,
        isFile: () => true,
        dev: 1,
        ino: 1,
      }),
      readFile: async () => Buffer.alloc(MAX_AUTHORING_PACK_BYTES + 1),
      realpath: fs.realpath,
    });
    assert.equal(growth.exitCode, 1);
    assert.equal(JSON.parse(growth.stdout).diagnostics[0].code, "PACK_INPUT_TOO_LARGE");
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("fatal UTF-8 and input retarget race fail without invoking compiler", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  let calls = 0;
  try {
    await fs.writeFile(path.join(moduleRoot, "source.json"), Buffer.from([0xc3, 0x28]));
    const malformed = await execute(moduleRoot, {
      args: ["source.json", "--output", "utf8", "--json"],
      compileAuthoringGamePackJson: async () => {
        calls += 1;
        return compiledResult();
      },
    });
    assert.equal(malformed.exitCode, 1);
    assert.equal(JSON.parse(malformed.stdout).diagnostics[0].code, "PACK_INPUT_UTF8_INVALID");

    await fs.writeFile(path.join(moduleRoot, "source.json"), "{}", "utf8");
    const realSource = path.join(moduleRoot, "source.json");
    let sourceRealpathCalls = 0;
    const raced = await execute(moduleRoot, {
      realpath: async (candidate) => {
        const resolved = await fs.realpath(candidate);
        if (path.resolve(candidate) === path.resolve(realSource)) {
          sourceRealpathCalls += 1;
          if (sourceRealpathCalls > 1) {
            return path.join(temporaryRoot, "outside.json");
          }
        }
        return resolved;
      },
      compileAuthoringGamePackJson: async () => {
        calls += 1;
        return compiledResult();
      },
    });
    assert.equal(raced.exitCode, 2);
    assert.equal(raced.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
    assert.equal(calls, 0);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("same-path input replacement is detected by bigint file identity", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const source = path.join(moduleRoot, "source.json");
  let replaced = false;
  try {
    const result = await execute(moduleRoot, {
      readFile: async (candidate) => {
        const bytes = await fs.readFile(candidate);
        if (!replaced && path.resolve(candidate) === path.resolve(source)) {
          replaced = true;
          const replacement = path.join(moduleRoot, "replacement.json");
          await fs.writeFile(replacement, '{"replacement":true}', "utf8");
          await fs.rename(replacement, source);
        }
        return bytes;
      },
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("external input and exports junctions are rejected before external access", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const external = path.join(temporaryRoot, "external");
  await fs.mkdir(external);
  await fs.writeFile(path.join(external, "source.json"), "{}", "utf8");
  try {
    await fs.symlink(
      external,
      path.join(moduleRoot, "linked"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const inputResult = await execute(moduleRoot, {
      args: ["linked/source.json", "--output", "outside"],
    });
    assert.equal(inputResult.exitCode, 2);
    assert.equal(inputResult.stderr, "PACK_COMPILE_CLI_PATH_OUTSIDE_MODULE\n");

    await fs.symlink(
      external,
      path.join(moduleRoot, "exports"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const outputResult = await execute(moduleRoot, {
      args: ["source.json", "--output", "outside"],
    });
    assert.equal(outputResult.exitCode, 2);
    assert.equal(outputResult.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
    assert.deepEqual((await fs.readdir(external)).sort(), ["source.json"]);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("pre-existing target or target junction is never overwritten", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const exportsRoot = path.join(moduleRoot, "exports");
  const external = path.join(temporaryRoot, "external-target");
  try {
    await fs.mkdir(exportsRoot);
    await fs.mkdir(path.join(exportsRoot, "occupied"));
    await fs.writeFile(path.join(exportsRoot, "occupied", "sentinel.txt"), "unchanged");
    const occupied = await execute(moduleRoot, {
      args: ["source.json", "--output", "occupied"],
    });
    assert.equal(occupied.stderr, "PACK_COMPILE_CLI_OUTPUT_EXISTS\n");
    assert.equal(
      await fs.readFile(path.join(exportsRoot, "occupied", "sentinel.txt"), "utf8"),
      "unchanged",
    );

    await fs.mkdir(external);
    await fs.writeFile(path.join(external, "sentinel.txt"), "external-unchanged");
    await fs.symlink(
      external,
      path.join(exportsRoot, "junction"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const junction = await execute(moduleRoot, {
      args: ["source.json", "--output", "junction"],
    });
    assert.equal(junction.stderr, "PACK_COMPILE_CLI_OUTPUT_EXISTS\n");
    assert.equal(
      await fs.readFile(path.join(external, "sentinel.txt"), "utf8"),
      "external-unchanged",
    );
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("second write, readback and publish failures leave no target and narrowly clean staging", async () => {
  for (const failure of ["write", "readback", "rename"]) {
    const { temporaryRoot, moduleRoot } = await createModule();
    const removed = [];
    let openCalls = 0;
    try {
      const result = await execute(moduleRoot, {
        args: ["source.json", "--output", failure],
        openFile: async (...args) => {
          openCalls += 1;
          const handle = await fs.open(...args);
          if (failure === "write" && openCalls === 2) {
            return fileHandleFacade(handle, {
              writeFile: async () => {
                throw new Error("private write failure");
              },
            });
          }
          if (failure === "readback" && openCalls === 1) {
            return fileHandleFacade(handle, {
              read: async (buffer, ...readArgs) => {
                const result = await handle.read(buffer, ...readArgs);
                if (result.bytesRead > 0) {
                  buffer[0] ^= 0xff;
                }
                return result;
              },
            });
          }
          return handle;
        },
        rename: async (from, to) => {
          if (failure === "rename") {
            throw new Error("private rename failure");
          }
          return fs.rename(from, to);
        },
        rm: async (candidate, options) => {
          removed.push(candidate);
          return fs.rm(candidate, options);
        },
      });
      assert.equal(result.exitCode, 2);
      assert.equal(result.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
      const exportsRoot = path.join(moduleRoot, "exports");
      await assert.rejects(fs.access(path.join(exportsRoot, failure)));
      assert.equal(removed.length, 1);
      assert.equal(path.dirname(removed[0]), exportsRoot);
      assert.equal(path.basename(removed[0]).startsWith(".matrix-oasis-"), true);
      assert.notEqual(path.resolve(removed[0]), path.resolve(exportsRoot));
      assert.notEqual(path.resolve(removed[0]), path.resolve(moduleRoot));
    } finally {
      await removeTemporaryRoot(temporaryRoot);
    }
  }
});

test("cleanup preserves a same-name staging replacement with a different bigint identity", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  let replacementPath;
  let rmCalls = 0;
  try {
    const result = await execute(moduleRoot, {
      args: ["source.json", "--output", "identity-replacement"],
      openFile: async (candidate) => {
        replacementPath = path.dirname(candidate);
        await fs.rm(replacementPath, { recursive: true, force: false });
        const donor = path.join(path.dirname(replacementPath), ".replacement-donor");
        await fs.mkdir(donor);
        await fs.writeFile(path.join(donor, "sentinel.txt"), "do-not-delete", "utf8");
        await fs.rename(donor, replacementPath);
        throw new Error("private replaced staging failure");
      },
      rm: async (...args) => {
        rmCalls += 1;
        return fs.rm(...args);
      },
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
    assert.equal(rmCalls, 0);
    assert.equal(
      await fs.readFile(path.join(replacementPath, "sentinel.txt"), "utf8"),
      "do-not-delete",
    );
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("junction replacement during exclusive open fails closed without publishing Pack bytes", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const external = path.join(temporaryRoot, "external-race");
  await fs.mkdir(external);
  let intercepted = false;
  let originalStaging;
  try {
    const result = await execute(moduleRoot, {
      args: ["source.json", "--output", "junction-race"],
      openFile: async (candidate, flags) => {
        if (!intercepted) {
          intercepted = true;
          const staging = path.dirname(candidate);
          originalStaging = `${staging}-original`;
          await fs.rename(staging, originalStaging);
          await fs.symlink(
            external,
            staging,
            process.platform === "win32" ? "junction" : "dir",
          );
        }
        return fs.open(candidate, flags);
      },
    });
    assert.equal(intercepted, true);
    assert.equal(result.exitCode, 2);
    assert.equal(result.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
    await assert.rejects(
      fs.access(path.join(moduleRoot, "exports", "junction-race")),
    );
    const externalNames = await fs.readdir(external);
    assert.deepEqual(externalNames, [RUNTIME_PACK_FILE_NAME]);
    assert.equal(
      (await fs.stat(path.join(external, RUNTIME_PACK_FILE_NAME))).size,
      0,
    );
    await assert.rejects(
      fs.access(path.join(external, RUNTIME_PACK_RECEIPT_FILE_NAME)),
    );
    assert.deepEqual(await fs.readdir(originalStaging), []);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("post-rename junction substitution is rejected and never recursively removed", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  const external = path.join(temporaryRoot, "external-final-target");
  await fs.mkdir(external);
  await fs.writeFile(path.join(external, "sentinel.txt"), "unchanged", "utf8");
  let capturedPair;
  let rmCalls = 0;
  try {
    const result = await execute(moduleRoot, {
      args: ["source.json", "--output", "final-junction"],
      rename: async (from, to) => {
        capturedPair = `${from}-captured`;
        await fs.rename(from, capturedPair);
        await fs.symlink(
          external,
          to,
          process.platform === "win32" ? "junction" : "dir",
        );
      },
      rm: async (...args) => {
        rmCalls += 1;
        return fs.rm(...args);
      },
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
    assert.equal(rmCalls, 0);
    const target = path.join(moduleRoot, "exports", "final-junction");
    assert.equal((await fs.lstat(target)).isSymbolicLink(), true);
    assert.deepEqual(await fs.readdir(external), ["sentinel.txt"]);
    assert.equal(
      await fs.readFile(path.join(external, "sentinel.txt"), "utf8"),
      "unchanged",
    );
    assert.deepEqual((await fs.readdir(capturedPair)).sort(), [
      RUNTIME_PACK_RECEIPT_FILE_NAME,
      RUNTIME_PACK_FILE_NAME,
    ]);
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("post-rename same-inode content tampering fails final handle readback", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  let rmCalls = 0;
  try {
    const target = path.join(moduleRoot, "exports", "tampered-final");
    const result = await execute(moduleRoot, {
      args: ["source.json", "--output", "tampered-final"],
      rename: async (from, to) => {
        await fs.rename(from, to);
        await fs.writeFile(
          path.join(to, RUNTIME_PACK_FILE_NAME),
          "tampered",
          "utf8",
        );
      },
      rm: async (...args) => {
        rmCalls += 1;
        return fs.rm(...args);
      },
    });
    assert.equal(result.exitCode, 2);
    assert.equal(result.stderr, "PACK_COMPILE_CLI_IO_ERROR\n");
    assert.equal(rmCalls, 0);
    assert.equal(
      await fs.readFile(path.join(target, RUNTIME_PACK_FILE_NAME), "utf8"),
      "tampered",
    );
    assert.equal(
      (await fs.stat(path.join(target, RUNTIME_PACK_RECEIPT_FILE_NAME))).isFile(),
      true,
    );
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("target race preserves raced bytes and same-slug concurrency publishes once", async () => {
  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    const target = path.join(moduleRoot, "exports", "raced");
    let renameCalls = 0;
    const raced = await execute(moduleRoot, {
      args: ["source.json", "--output", "raced"],
      rename: async (from, to) => {
        renameCalls += 1;
        assert.equal(path.resolve(to), path.resolve(target));
        await fs.mkdir(to);
        await fs.writeFile(path.join(to, "sentinel.txt"), "raced-unchanged");
        return fs.rename(from, to);
      },
    });
    assert.equal(renameCalls, 1);
    assert.equal(raced.exitCode, 2);
    assert.equal(await fs.readFile(path.join(target, "sentinel.txt"), "utf8"), "raced-unchanged");

    const results = await Promise.all([
      execute(moduleRoot, { args: ["source.json", "--output", "concurrent"] }),
      execute(moduleRoot, { args: ["source.json", "--output", "concurrent"] }),
    ]);
    assert.deepEqual(
      results.map(({ exitCode }) => exitCode).sort(),
      [0, 2],
    );
    assert.equal(
      results.find(({ exitCode }) => exitCode === 2).stderr ===
        "PACK_COMPILE_CLI_OUTPUT_EXISTS\n" ||
        results.find(({ exitCode }) => exitCode === 2).stderr ===
          "PACK_COMPILE_CLI_IO_ERROR\n",
      true,
    );
    assert.deepEqual(
      (await fs.readdir(path.join(moduleRoot, "exports", "concurrent"))).sort(),
      [RUNTIME_PACK_RECEIPT_FILE_NAME, RUNTIME_PACK_FILE_NAME],
    );
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("compiler throws and malformed dynamic results are statically redacted", async () => {
  const sentinel = "private-path-api-key-value";
  for (const compileAuthoringGamePackJson of [
    async () => {
      throw new Error(sentinel);
    },
    async () => {
      const error = new Error(sentinel);
      error.code = "PACK_COMPILER_INTERNAL_ERROR";
      throw error;
    },
    async () => ({ ok: true, runtimePack: { secret: sentinel } }),
    async () => ({
      ok: false,
      validationReport: {
        ...invalidAuthoringReport(),
        diagnostics: [
          {
            ...invalidAuthoringReport().diagnostics[0],
            code: sentinel,
          },
        ],
      },
    }),
  ]) {
    const { temporaryRoot, moduleRoot } = await createModule();
    try {
      const result = await execute(moduleRoot, { compileAuthoringGamePackJson });
      assert.equal(result.exitCode, 2);
      assert.equal(result.stdout.includes(sentinel), false);
      assert.equal(result.stderr.includes(sentinel), false);
      assert.equal(result.stderr, "PACK_COMPILE_CLI_INTERNAL_ERROR\n");
    } finally {
      await removeTemporaryRoot(temporaryRoot);
    }
  }

  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    const declared = await execute(moduleRoot, {
      compileAuthoringGamePackJson: async () => {
        throw new CompilerOperationalError(sentinel);
      },
    });
    assert.deepEqual(declared, {
      exitCode: 2,
      stdout: "",
      stderr: "PACK_COMPILER_INTERNAL_ERROR\n",
    });
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test("both frozen examples publish canonical pairs that pass the public Runtime Validator", async () => {
  const exampleNames = [
    "mechanics-conformance.authoring-game-pack.json",
    "last-train-r1.authoring-game-pack.json",
  ];
  const { temporaryRoot, moduleRoot } = await createModule();
  try {
    for (const [index, exampleName] of exampleNames.entries()) {
      const inputName = `frozen-example-${index + 1}.json`;
      const outputSlug = `frozen-example-${index + 1}`;
      const sourceBytes = await fs.readFile(
        path.join(realModuleRoot, "examples", exampleName),
      );
      await fs.writeFile(path.join(moduleRoot, inputName), sourceBytes, {
        flag: "wx",
      });

      const result = await execute(moduleRoot, {
        args: [inputName, "--output", outputSlug, "--json"],
        compileAuthoringGamePackJson,
        GamePackCompilerOperationalError: PublicCompilerOperationalError,
        validateRuntimeGamePackJson,
        RuntimeGamePackValidatorOperationalError:
          PublicValidatorOperationalError,
      });
      assert.equal(result.exitCode, 0);
      assert.equal(result.stderr, "");
      assert.equal(result.stdout.trim().split(/\r?\n/).length, 1);

      const publishedRoot = path.join(moduleRoot, "exports", outputSlug);
      assert.deepEqual((await fs.readdir(publishedRoot)).sort(), [
        RUNTIME_PACK_RECEIPT_FILE_NAME,
        RUNTIME_PACK_FILE_NAME,
      ]);
      const [runtimeText, receiptText] = await Promise.all([
        fs.readFile(path.join(publishedRoot, RUNTIME_PACK_FILE_NAME), "utf8"),
        fs.readFile(
          path.join(publishedRoot, RUNTIME_PACK_RECEIPT_FILE_NAME),
          "utf8",
        ),
      ]);
      assert.equal(runtimeText, canonicalizeJsonValue(JSON.parse(runtimeText)));
      assert.equal(receiptText, canonicalizeJsonValue(JSON.parse(receiptText)));
      assert.equal(runtimeText.startsWith("\uFEFF"), false);
      assert.equal(receiptText.startsWith("\uFEFF"), false);
      assert.equal(/[\r\n]$/u.test(runtimeText), false);
      assert.equal(/[\r\n]$/u.test(receiptText), false);
      assert.deepEqual(await validateRuntimeGamePackJson(runtimeText, receiptText), {
        reportVersion: 1,
        valid: true,
        diagnostics: [],
      });

      const cliSuccess = JSON.parse(result.stdout);
      assert.deepEqual(cliSuccess.receipt, JSON.parse(receiptText));
    }
  } finally {
    await removeTemporaryRoot(temporaryRoot);
  }
});

test(
  "real compile CLI resolves workspaces and returns one invalid JSON line without publishing",
  { skip: !fsSync.existsSync(path.join(realModuleRoot, "node_modules", "@matrix-oasis", "game-pack-compiler")) },
  () => {
    const result = spawnSync(
      process.execPath,
      ["scripts/compile-pack.mjs", "package.json", "--output", "invalid-real", "--json"],
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
    assert.equal(JSON.parse(result.stdout).valid, false);
  },
);
