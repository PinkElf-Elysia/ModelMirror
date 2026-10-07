import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const clientRoot = fileURLToPath(new URL("../", import.meta.url));

function readConfig(root, name) {
  const path = join(root, name);
  const result = ts.readConfigFile(path, ts.sys.readFile);
  assert.equal(result.error, undefined);
  const parsed = ts.parseJsonConfigFileContent(result.config, ts.sys, root, {}, path);
  assert.deepEqual(parsed.errors, []);
  return parsed;
}

function comparableOptions(options) {
  const { tsBuildInfoFile, configFilePath, ...rest } = options;
  return rest;
}

function isTestRoot(path) {
  const local = relative(clientRoot, path).replaceAll("\\", "/");
  return local.startsWith("src/test/") || /\.(test|spec)\.tsx?$/.test(local);
}

function withFixture(files, run) {
  const root = mkdtempSync(join(tmpdir(), "modelmirror-build-boundary-"));
  try {
    for (const name of ["tsconfig.app.json", "tsconfig.build.json"]) {
      copyFileSync(join(clientRoot, name), join(root, name));
    }
    for (const [name, content] of Object.entries(files)) {
      const path = join(root, name);
      mkdirSync(dirname(path), { recursive: true });
      writeFileSync(path, content);
    }
    return run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function diagnostics(root, config) {
  const parsed = readConfig(root, config);
  const program = ts.createProgram({ rootNames: parsed.fileNames, options: parsed.options });
  return ts.getPreEmitDiagnostics(program).map((item) => ({
    code: item.code,
    file: item.file ? relative(root, item.file.fileName).replaceAll("\\", "/") : null,
  }));
}

test("production roots exclude only tests and retain every other application root", () => {
  const full = readConfig(clientRoot, "tsconfig.app.json");
  const build = readConfig(clientRoot, "tsconfig.build.json");
  assert.ok(full.fileNames.some(isTestRoot));
  assert.ok(full.fileNames.some((path) => /RpgModelSelector\.test\.tsx$/.test(path)));
  assert.deepEqual(build.fileNames, full.fileNames.filter((path) => !isTestRoot(path)));
  assert.deepEqual(comparableOptions(build.options), comparableOptions(full.options));
  assert.equal(build.options.strict, true);
  assert.notEqual(build.options.tsBuildInfoFile, full.options.tsBuildInfoFile);
});

test("CI typecheck retains the full project and production build checks Vite config", () => {
  const { scripts } = JSON.parse(readFileSync(join(clientRoot, "package.json"), "utf8"));
  const project = readConfig(clientRoot, "tsconfig.json");
  const node = readConfig(clientRoot, "tsconfig.node.json");
  assert.equal(scripts.typecheck, "tsc -b --pretty false");
  assert.equal(scripts.build, "tsc -b tsconfig.build.json tsconfig.node.json && vite build");
  assert.deepEqual(project.projectReferences.map((ref) => relative(clientRoot, ref.path)), [
    "tsconfig.app.json", "tsconfig.node.json",
  ]);
  assert.ok(node.fileNames.some((path) => path.endsWith("vite.config.ts")));
  assert.equal(node.options.strict, true);
  assert.ok(scripts["test:run"].includes("scripts/build-typecheck.node.mjs"));
});

test("a test type error still fails full typecheck but is not a production root", () => {
  withFixture({
    "src/main.ts": "export const value: number = 1;",
    "src/value.test.ts": "export const value: number = 'wrong';",
  }, (root) => {
    assert.deepEqual(diagnostics(root, "tsconfig.build.json"), []);
    assert.deepEqual(diagnostics(root, "tsconfig.app.json"), [{ code: 2322, file: "src/value.test.ts" }]);
  });
});

test("production type errors fail both configurations", () => {
  withFixture({ "src/main.ts": "export const value: number = 'wrong';" }, (root) => {
    for (const config of ["tsconfig.app.json", "tsconfig.build.json"]) {
      assert.deepEqual(diagnostics(root, config), [{ code: 2322, file: "src/main.ts" }]);
    }
  });
});

test("an excluded test imported by production is still typechecked", () => {
  withFixture({
    "src/main.ts": "export { value } from './value.test';",
    "src/value.test.ts": "export const value: number = 'wrong';",
  }, (root) => {
    assert.deepEqual(diagnostics(root, "tsconfig.build.json"), [{ code: 2322, file: "src/value.test.ts" }]);
  });
});

test("missing out-of-context test dependencies fail only the full configuration", () => {
  withFixture({
    "src/main.ts": "export const value: number = 1;",
    "src/value.test.ts": "export { value } from '../../experiments/test-only-module';",
  }, (root) => {
    assert.deepEqual(diagnostics(root, "tsconfig.build.json"), []);
    assert.deepEqual(diagnostics(root, "tsconfig.app.json"), [{ code: 2307, file: "src/value.test.ts" }]);
  });
});
