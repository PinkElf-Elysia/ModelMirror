import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  assertDirtyStatusWithinModule,
  parsePorcelainV1Z,
} from "../scripts/verify-extraction.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const modulePrefix = "experiments/matrix-oasis-engine";

function runGit(cwd, args, options = {}) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: options.encoding ?? "utf8",
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`Git fixture step failed: ${args[0]}`);
  }
  return result.stdout;
}

async function createRenameFixture(t) {
  const fixtureRoot = await fs.mkdtemp(
    path.join(os.tmpdir(), "matrix-oasis-status-fixture-"),
  );
  t.after(async () => {
    await fs.rm(fixtureRoot, { recursive: true, force: true, maxRetries: 3 });
  });

  await fs.mkdir(path.join(fixtureRoot, modulePrefix), { recursive: true });
  await fs.writeFile(
    path.join(fixtureRoot, modulePrefix, "source file.txt"),
    "fixture\n",
    "utf8",
  );
  runGit(fixtureRoot, ["init", "--quiet"]);
  runGit(fixtureRoot, ["add", "."]);
  runGit(fixtureRoot, [
    "-c",
    "user.name=Matrix Oasis Tests",
    "-c",
    "user.email=matrix-oasis-tests@example.invalid",
    "commit",
    "--quiet",
    "-m",
    "fixture",
  ]);
  return fixtureRoot;
}

function readNulStatus(repositoryRoot) {
  return runGit(
    repositoryRoot,
    ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    { encoding: "buffer" },
  );
}

test("extraction is explicit and never recursive through verify", async () => {
  const manifest = JSON.parse(
    await fs.readFile(path.join(moduleRoot, "package.json"), "utf8"),
  );

  assert.equal(manifest.scripts["verify:extraction"], "node scripts/verify-extraction.mjs");
  assert.equal(manifest.scripts.verify.includes("verify:extraction"), false);
});

test("extraction script preserves history and verifies a clean standalone root", async () => {
  const source = await fs.readFile(
    path.join(moduleRoot, "scripts", "verify-extraction.mjs"),
    "utf8",
  );

  for (const required of [
    '"--shared"',
    '"--no-local"',
    "subtree",
    "split",
    "npm-ci",
    "npm-prefix",
    "npm-ls",
    "standalone-verify",
    "standalone-smoke",
    "source-archive",
    "archiveSha256",
    "EXTRACTION_TEMP_PRESERVED",
  ]) {
    assert.match(source, new RegExp(required));
  }

  assert.match(
    source,
    /\["clone", "--shared", "--no-checkout"/,
    "the disposable source clone should reuse the local object store",
  );
  assert.match(
    source,
    /"--no-local",[\s\S]*?"--single-branch"/,
    "the standalone clone must copy objects rather than retain local alternates",
  );
  assert.match(
    source,
    /\["status", "--porcelain=v1", "-z", "--untracked-files=all"\]/,
    "dirty-scope inspection must use NUL-delimited porcelain output",
  );
});

test("NUL-delimited status parsing preserves spaces and arrow text", () => {
  const status = Buffer.from(
    `R  ${modulePrefix}/new -> display name.txt\0${modulePrefix}/old name.txt\0`,
    "utf8",
  );

  assert.deepEqual(parsePorcelainV1Z(status), [
    {
      status: "R ",
      paths: [
        `${modulePrefix}/new -> display name.txt`,
        `${modulePrefix}/old name.txt`,
      ],
    },
  ]);
  assert.doesNotThrow(() => assertDirtyStatusWithinModule(status, modulePrefix));
});

test("status parsing fails closed for incomplete or non-NUL records", () => {
  assert.throws(
    () => parsePorcelainV1Z(Buffer.from(`R  ${modulePrefix}/new.txt\0`, "utf8")),
    /PORCELAIN_STATUS_INCOMPLETE_RENAME/,
  );
  assert.throws(
    () => parsePorcelainV1Z(` M ${modulePrefix}/path with spaces.txt`),
    /PORCELAIN_STATUS_MISSING_TERMINATOR/,
  );
});

test("a staged rename from the module into the parent scope is rejected", async (t) => {
  const fixtureRoot = await createRenameFixture(t);
  await fs.mkdir(path.join(fixtureRoot, "client"), { recursive: true });
  runGit(fixtureRoot, [
    "mv",
    `${modulePrefix}/source file.txt`,
    "client/escaped file.txt",
  ]);

  const status = readNulStatus(fixtureRoot);
  const parsedPaths = parsePorcelainV1Z(status).flatMap((entry) => entry.paths);
  assert.equal(parsedPaths.includes("client/escaped file.txt"), true);
  assert.equal(parsedPaths.includes(`${modulePrefix}/source file.txt`), true);
  assert.throws(
    () => assertDirtyStatusWithinModule(status, modulePrefix),
    /EXTRACTION_DIRTY_SCOPE_VIOLATION/,
  );
});

test("a staged rename wholly inside the module is allowed", async (t) => {
  const fixtureRoot = await createRenameFixture(t);
  await fs.mkdir(path.join(fixtureRoot, modulePrefix, "renamed folder"), {
    recursive: true,
  });
  runGit(fixtureRoot, [
    "mv",
    `${modulePrefix}/source file.txt`,
    `${modulePrefix}/renamed folder/target file.txt`,
  ]);

  const status = readNulStatus(fixtureRoot);
  const entries = assertDirtyStatusWithinModule(status, modulePrefix);
  assert.equal(entries.length, 1);
  assert.deepEqual(new Set(entries[0].paths), new Set([
    `${modulePrefix}/source file.txt`,
    `${modulePrefix}/renamed folder/target file.txt`,
  ]));
});
