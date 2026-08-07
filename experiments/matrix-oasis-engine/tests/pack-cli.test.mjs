import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  MAX_PACK_BYTES,
  PackCliOperationalError,
  executePackCli,
  parsePackCliArgs,
} from "../scripts/lib/pack-input-core.mjs";

const moduleRoot = path.resolve("virtual-pack-module");
const realModuleRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const inputName = ["fixtures", "pack.json"].join("/");

class ValidatorOperationalError extends Error {
  constructor() {
    super("not for CLI output");
    this.code = "PACK_VALIDATOR_INTERNAL_ERROR";
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
        phase: "semantic",
        severity: "error",
        code: "PACK_REFERENCE_MISSING",
        path: "/entryNodeId",
        message: "The referenced node does not exist.",
      },
    ],
  };
}

async function runHarness({
  args = [inputName],
  bytes = Buffer.from("{}", "utf8"),
  size = bytes.byteLength,
  isFile = true,
  resolvedCandidate,
  realpathError,
  readError,
  report = validReport(),
  validate,
} = {}) {
  const calls = { read: 0, validate: [] };
  const result = await executePackCli({
    args,
    moduleRoot,
    realpath: async (candidate) => {
      if (realpathError) {
        throw realpathError;
      }
      if (candidate === moduleRoot) {
        return moduleRoot;
      }
      return resolvedCandidate ?? candidate;
    },
    stat: async () => ({ size, isFile: () => isFile }),
    readFile: async () => {
      calls.read += 1;
      if (readError) {
        throw readError;
      }
      return bytes;
    },
    validateAuthoringGamePackJson: (text) => {
      calls.validate.push(text);
      return validate ? validate(text) : report;
    },
    AuthoringGamePackOperationalError: ValidatorOperationalError,
  });
  return { result, calls };
}

function expectArgumentCode(args, code) {
  assert.throws(
    () => parsePackCliArgs(args),
    (error) => error instanceof PackCliOperationalError && error.code === code,
  );
}

test("argument parser accepts one relative JSON path and --json in either position", () => {
  assert.deepEqual(parsePackCliArgs([inputName]), { input: inputName, json: false });
  assert.deepEqual(parsePackCliArgs(["--json", inputName]), {
    input: inputName,
    json: true,
  });
  assert.deepEqual(parsePackCliArgs([inputName, "--json"]), {
    input: inputName,
    json: true,
  });

  const separator = String.fromCharCode(92);
  const windowsRelative = ["fixtures", "PACK.JSON"].join(separator);
  assert.deepEqual(parsePackCliArgs([windowsRelative]), {
    input: "fixtures/PACK.JSON",
    json: false,
  });
});

test("argument parser rejects missing, duplicate, unknown, multiple, NUL and non-JSON inputs", () => {
  expectArgumentCode([], "PACK_CLI_INPUT_REQUIRED");
  expectArgumentCode(["--json"], "PACK_CLI_INPUT_REQUIRED");
  expectArgumentCode(["--json", "--json", inputName], "PACK_CLI_ARGUMENT_INVALID");
  expectArgumentCode(["--machine", inputName], "PACK_CLI_UNKNOWN_OPTION");
  expectArgumentCode([inputName, "second.json"], "PACK_CLI_MULTIPLE_INPUTS");
  expectArgumentCode(
    [`pack${String.fromCharCode(0)}.json`],
    "PACK_CLI_ARGUMENT_INVALID",
  );
  expectArgumentCode(["pack.txt"], "PACK_CLI_EXTENSION_INVALID");
});

test("argument parser rejects traversal and Windows or POSIX absolute forms", () => {
  const parentSegment = ".".repeat(2);
  expectArgumentCode(
    [[parentSegment, "pack.json"].join("/")],
    "PACK_CLI_PATH_TRAVERSAL",
  );

  const separator = String.fromCharCode(92);
  const windowsAbsolute = ["C:", "fixture", "pack.json"].join(separator);
  expectArgumentCode([windowsAbsolute], "PACK_CLI_PATH_NOT_RELATIVE");

  const posixAbsolute = ["", "fixture", "pack.json"].join("/");
  expectArgumentCode([posixAbsolute], "PACK_CLI_PATH_NOT_RELATIVE");
});

test("contained regular UTF-8 input is validated and human success is stable", async () => {
  const bytes = Buffer.from('{"title":"generic"}', "utf8");
  const { result, calls } = await runHarness({ bytes });

  assert.equal(result.exitCode, 0);
  assert.equal(result.stdout, "PACK_VALID\n");
  assert.equal(result.stderr, "");
  assert.deepEqual(calls.validate, ['{"title":"generic"}']);
});

test("--json writes exactly one report to stdout for valid and invalid content", async () => {
  for (const report of [validReport(), invalidReport()]) {
    const { result } = await runHarness({ args: ["--json", inputName], report });
    assert.equal(result.exitCode, report.valid ? 0 : 1);
    assert.equal(result.stdout, `${JSON.stringify(report)}\n`);
    assert.equal(result.stderr, "");
    assert.equal(result.stdout.trim().split(/\r?\n/).length, 1);
  }
});

test("human invalid output is deterministic and does not echo diagnostic messages", async () => {
  const { result } = await runHarness({ report: invalidReport() });
  assert.equal(result.exitCode, 1);
  assert.equal(result.stdout, "");
  assert.equal(
    result.stderr,
    "PACK_INVALID semantic PACK_REFERENCE_MISSING /entryNodeId\n",
  );
  assert.equal(result.stderr.includes("does not exist"), false);
});

test("allowed optional diagnostic fields are rebuilt in the fixed public order", async () => {
  const report = invalidReport();
  report.diagnostics[0].relatedPath = "/nodes/0/id";
  report.diagnostics[0].location = { line: 2, column: 3 };
  const expected = {
    reportVersion: 1,
    valid: false,
    diagnostics: [
      {
        phase: "semantic",
        severity: "error",
        code: "PACK_REFERENCE_MISSING",
        path: "/entryNodeId",
        message: "The referenced node does not exist.",
        relatedPath: "/nodes/0/id",
        location: { line: 2, column: 3 },
      },
    ],
  };
  const { result } = await runHarness({ args: ["--json", inputName], report });
  assert.equal(result.stdout, `${JSON.stringify(expected)}\n`);
  assert.equal(result.stderr, "");
});

test("one MiB is accepted while larger input becomes a stable parse diagnostic", async () => {
  const exactBytes = Buffer.alloc(MAX_PACK_BYTES, 32);
  const exact = await runHarness({ bytes: exactBytes, size: MAX_PACK_BYTES });
  assert.equal(exact.result.exitCode, 0);
  assert.equal(exact.calls.read, 1);

  const oversized = await runHarness({
    args: ["--json", inputName],
    bytes: Buffer.alloc(1),
    size: MAX_PACK_BYTES + 1,
  });
  assert.equal(oversized.result.exitCode, 1);
  assert.equal(oversized.calls.read, 0);
  assert.equal(oversized.result.stderr, "");
  assert.deepEqual(JSON.parse(oversized.result.stdout), {
    reportVersion: 1,
    valid: false,
    diagnostics: [
      {
        phase: "parse",
        severity: "error",
        code: "PACK_INPUT_TOO_LARGE",
        path: "",
        message: "Input exceeds the 1 MiB limit.",
      },
    ],
  });
});

test("post-read size recheck rejects a file that grew after stat", async () => {
  const { result, calls } = await runHarness({
    bytes: Buffer.alloc(MAX_PACK_BYTES + 1),
    size: 1,
  });
  assert.equal(result.exitCode, 1);
  assert.equal(calls.read, 1);
  assert.equal(calls.validate.length, 0);
  assert.equal(
    result.stderr,
    "PACK_INVALID parse PACK_INPUT_TOO_LARGE /\n",
  );
});

test("malformed UTF-8 is a content-invalid parse diagnostic", async () => {
  const { result, calls } = await runHarness({
    args: ["--json", inputName],
    bytes: Buffer.from([0xc3, 0x28]),
  });
  assert.equal(result.exitCode, 1);
  assert.equal(calls.validate.length, 0);
  const report = JSON.parse(result.stdout);
  assert.equal(report.diagnostics[0].phase, "parse");
  assert.equal(report.diagnostics[0].code, "PACK_INPUT_UTF8_INVALID");
});

test("realpath containment rejects an external symlink target before reading", async () => {
  const parentSegment = ".".repeat(2);
  const outside = path.resolve(moduleRoot, parentSegment, "external", "pack.json");
  const { result, calls } = await runHarness({ resolvedCandidate: outside });
  assert.deepEqual(result, {
    exitCode: 2,
    stdout: "",
    stderr: "PACK_CLI_PATH_OUTSIDE_MODULE\n",
  });
  assert.equal(calls.read, 0);
});

test("real filesystem checks accept files and reject directories or renamed link targets", async () => {
  const temporaryPrefix = "matrix-oasis-pack-cli-";
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), temporaryPrefix));
  const realModuleRoot = path.join(temporaryRoot, "module");
  const validPath = path.join(realModuleRoot, "valid.json");
  const directoryPath = path.join(realModuleRoot, "directory.json");
  const targetDirectory = path.join(realModuleRoot, "target.txt");
  const linkPath = path.join(realModuleRoot, "linked.json");

  try {
    await fs.mkdir(realModuleRoot, { recursive: true });
    await fs.writeFile(validPath, "{}", "utf8");
    await fs.mkdir(directoryPath);
    await fs.mkdir(targetDirectory);
    await fs.symlink(
      targetDirectory,
      linkPath,
      process.platform === "win32" ? "junction" : "dir",
    );

    const common = {
      moduleRoot: realModuleRoot,
      readFile: fs.readFile,
      realpath: fs.realpath,
      stat: fs.stat,
      validateAuthoringGamePackJson: validReport,
      AuthoringGamePackOperationalError: ValidatorOperationalError,
    };
    const valid = await executePackCli({ ...common, args: ["valid.json"] });
    assert.equal(valid.exitCode, 0);

    const directory = await executePackCli({
      ...common,
      args: ["directory.json"],
    });
    assert.equal(directory.exitCode, 2);
    assert.equal(directory.stderr, "PACK_CLI_INPUT_NOT_FILE\n");

    const linked = await executePackCli({ ...common, args: ["linked.json"] });
    assert.equal(linked.exitCode, 2);
    assert.equal(linked.stderr, "PACK_CLI_EXTENSION_INVALID\n");
  } finally {
    const temporaryBase = path.resolve(os.tmpdir());
    const resolvedTemporaryRoot = path.resolve(temporaryRoot);
    assert.equal(path.basename(resolvedTemporaryRoot).startsWith(temporaryPrefix), true);
    assert.equal(resolvedTemporaryRoot.startsWith(`${temporaryBase}${path.sep}`), true);
    await fs.rm(resolvedTemporaryRoot, { recursive: true, force: true });
  }
});

test("directories, I/O errors and validator failures remain operational errors", async () => {
  const directory = await runHarness({ isFile: false });
  assert.equal(directory.result.stderr, "PACK_CLI_INPUT_NOT_FILE\n");

  const sensitiveDetail = ["private", "fixture", "value"].join("-");
  const ioFailure = await runHarness({ realpathError: new Error(sensitiveDetail) });
  assert.deepEqual(ioFailure.result, {
    exitCode: 2,
    stdout: "",
    stderr: "PACK_CLI_IO_ERROR\n",
  });
  assert.equal(ioFailure.result.stderr.includes(sensitiveDetail), false);

  const validatorFailure = await runHarness({
    validate: () => {
      throw new Error(sensitiveDetail);
    },
  });
  assert.equal(validatorFailure.result.exitCode, 2);
  assert.equal(validatorFailure.result.stderr, "PACK_CLI_INTERNAL_ERROR\n");
  assert.equal(validatorFailure.result.stderr.includes(sensitiveDetail), false);

  const forgedCodeFailure = await runHarness({
    validate: () => {
      const error = new Error(sensitiveDetail);
      error.code = "PACK_VALIDATOR_INTERNAL_ERROR";
      throw error;
    },
  });
  assert.equal(forgedCodeFailure.result.stderr, "PACK_CLI_INTERNAL_ERROR\n");

  const declaredValidatorFailure = await runHarness({
    validate: () => {
      throw new ValidatorOperationalError();
    },
  });
  assert.equal(declaredValidatorFailure.result.exitCode, 2);
  assert.equal(
    declaredValidatorFailure.result.stderr,
    "PACK_VALIDATOR_INTERNAL_ERROR\n",
  );
});

test("malformed validator reports fail closed as internal errors", async () => {
  for (const report of [
    null,
    {},
    { reportVersion: 1, valid: true },
    { reportVersion: 1, valid: false, diagnostics: [] },
  ]) {
    const { result } = await runHarness({ report });
    assert.deepEqual(result, {
      exitCode: 2,
      stdout: "",
      stderr: "PACK_CLI_INTERNAL_ERROR\n",
    });
  }
});

test("extra report fields fail closed without leaking dynamic sentinels", async () => {
  const sentinel = ["dynamic", "private", "sentinel"].join("-");
  const topLevel = { ...validReport(), [sentinel]: sentinel };

  const diagnosticLevel = invalidReport();
  diagnosticLevel.diagnostics[0][sentinel] = sentinel;

  const locationLevel = invalidReport();
  locationLevel.diagnostics[0].location = {
    line: 1,
    column: 1,
    [sentinel]: sentinel,
  };

  for (const report of [topLevel, diagnosticLevel, locationLevel]) {
    const { result } = await runHarness({ args: ["--json", inputName], report });
    assert.deepEqual(result, {
      exitCode: 2,
      stdout: "",
      stderr: "PACK_CLI_INTERNAL_ERROR\n",
    });
    assert.equal(result.stdout.includes(sentinel), false);
    assert.equal(result.stderr.includes(sentinel), false);
  }
});

test("related paths and locations enforce their exact public types", async () => {
  const invalidRelatedPath = invalidReport();
  invalidRelatedPath.diagnostics[0].relatedPath = 1;

  const zeroLocation = invalidReport();
  zeroLocation.diagnostics[0].location = { line: 0, column: 1 };

  const unsafeLocation = invalidReport();
  unsafeLocation.diagnostics[0].location = {
    line: Number.MAX_SAFE_INTEGER + 1,
    column: 1,
  };

  for (const report of [invalidRelatedPath, zeroLocation, unsafeLocation]) {
    const { result } = await runHarness({ report });
    assert.deepEqual(result, {
      exitCode: 2,
      stdout: "",
      stderr: "PACK_CLI_INTERNAL_ERROR\n",
    });
  }
});

test("real CLI resolves the installed validator workspace without leaking npm output", () => {
  const result = spawnSync(
    process.execPath,
    ["scripts/validate-pack.mjs", "package.json", "--json"],
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
  const report = JSON.parse(result.stdout);
  assert.equal(report.reportVersion, 1);
  assert.equal(report.valid, false);
  assert.equal(report.diagnostics.length > 0, true);
  assert.equal(report.diagnostics.every(({ phase }) => phase === "schema"), true);
});
