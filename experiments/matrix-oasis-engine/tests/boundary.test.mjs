import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const checkerPath = path.join(moduleRoot, "scripts", "check-boundary.mjs");
const fixturePrefix = "matrix-oasis-boundary-";
const committedPolicy = JSON.parse(
  await fs.readFile(path.join(moduleRoot, "module-boundary.json"), "utf8"),
);
const parentSegment = ".".repeat(2);

function escapePath(...segments) {
  return [parentSegment, ...segments].join("/");
}

function staticParentJoinSource(...segments) {
  return `[${[parentSegment, ...segments].map((value) => JSON.stringify(value)).join(", ")}].join("/")`;
}

function validSmokeSource(extra = "") {
  return [
    'import net from "node:net";',
    'import { preview } from "vite";',
    'const LOOPBACK_HOST = "127.0.0.1";',
    "const server = net.createServer();",
    "server.listen(0, LOOPBACK_HOST, () => {});",
    "const port = 4173;",
    'const url = `http://${LOOPBACK_HOST}:${port}/`;',
    "preview({ preview: { host: LOOPBACK_HOST, port } });",
    "fetch(url, { method: \"GET\" });",
    extra,
    "",
  ].join("\n");
}

function boundaryPolicy() {
  return structuredClone(committedPolicy);
}

function privatePackage(overrides = {}) {
  return {
    name: "@matrix-oasis/boundary-fixture",
    version: "0.0.0-r1",
    private: true,
    license: "UNLICENSED",
    type: "module",
    ...overrides,
  };
}

async function writeJson(target, value) {
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

async function createFixture() {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), fixturePrefix));
  const root = path.join(temporaryRoot, "module");
  await fs.mkdir(root, { recursive: true });
  const init = spawnSync("git", ["init", "--quiet"], {
    cwd: root,
    encoding: "utf8",
    shell: false,
    windowsHide: true,
  });
  assert.equal(init.status, 0, init.stderr);
  await writeJson(path.join(root, "module-boundary.json"), boundaryPolicy());
  await writeJson(path.join(root, "package.json"), privatePackage());
  return { temporaryRoot, root, links: [] };
}

async function cleanupFixture(fixture) {
  for (const link of fixture.links.reverse()) {
    await fs.unlink(link).catch(() => undefined);
  }
  const temporaryBase = path.resolve(os.tmpdir());
  const resolved = path.resolve(fixture.temporaryRoot);
  assert.equal(path.basename(resolved).startsWith(fixturePrefix), true);
  assert.equal(resolved.startsWith(`${temporaryBase}${path.sep}`), true);
  await fs.rm(resolved, { recursive: true, force: true });
}

function runChecker(root) {
  const result = spawnSync(
    process.execPath,
    [checkerPath, "--root", root, "--json"],
    {
      cwd: root,
      encoding: "utf8",
      shell: false,
      windowsHide: true,
    },
  );
  return {
    status: result.status,
    stdout: result.stdout,
    stderr: result.stderr,
    report: result.stdout ? JSON.parse(result.stdout) : null,
  };
}

async function withFixture(callback) {
  const fixture = await createFixture();
  try {
    return await callback(fixture);
  } finally {
    await cleanupFixture(fixture);
  }
}

test("the committed module satisfies its own boundary", () => {
  const result = runChecker(moduleRoot);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(result.report.ok, true);
  assert.deepEqual(result.report.violations, []);
});

test("a valid isolated fixture passes", async () => {
  await withFixture(async ({ root, links }) => {
    await writeJson(
      path.join(root, "package.json"),
      privatePackage({
        workspaces: ["packages/*"],
        dependencies: { "@matrix-oasis/shared": "file:./packages/shared" },
      }),
    );
    await writeJson(
      path.join(root, "packages", "shared", "package.json"),
      privatePackage({ name: "@matrix-oasis/shared" }),
    );
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "src", "main.js"),
      'import { value } from "./value.js";\nexport { value };\n',
      "utf8",
    );
    await fs.writeFile(path.join(root, "src", "value.js"), "export const value = 1;\n");
    await fs.writeFile(path.join(root, "src", "texture.png"), "fixture");
    await fs.writeFile(
      path.join(root, "src", "styles.css"),
      '.surface { background-image: url("./texture.png"); }\n',
      "utf8",
    );
    await fs.mkdir(path.join(root, "apps", "creator-web"), { recursive: true });
    await fs.writeFile(
      path.join(root, "apps", "creator-web", "index.html"),
      '<script type="module" src="/src/main.tsx"></script>\n',
      "utf8",
    );
    await fs.mkdir(path.join(root, "apps", "runtime-godot", "scenes"), {
      recursive: true,
    });
    await fs.writeFile(
      path.join(root, "apps", "runtime-godot", "project.godot"),
      '[application]\nconfig/name="Fixture"\n',
      "utf8",
    );
    await fs.writeFile(
      path.join(root, "apps", "runtime-godot", "bootstrap.gd"),
      "extends Node\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(root, "apps", "runtime-godot", "bootstrap.gd.uid"),
      "uid://fixtureidentity\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(root, "apps", "runtime-godot", "scenes", "bootstrap.tscn"),
      "[gd_scene format=3]\n",
      "utf8",
    );
    await fs.mkdir(
      path.join(root, "apps", "runtime-godot", "addons", "gdUnit4"),
      { recursive: true },
    );
    await fs.writeFile(
      path.join(root, "apps", "runtime-godot", "addons", "gdUnit4", "plugin.gd"),
      "@tool\nextends EditorPlugin\n",
      "utf8",
    );
    await fs.writeFile(
      path.join(root, "apps", "runtime-godot", "addons", "gdUnit4", "fixture.scn"),
      Buffer.from([0x47, 0x44, 0x53, 0x43]),
    );

    const internalLink = path.join(root, "linked-shared");
    await fs.symlink(
      path.join(root, "packages", "shared"),
      internalLink,
      process.platform === "win32" ? "junction" : "dir",
    );
    links.push(internalLink);

    const result = runChecker(root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.equal(result.report.ok, true);
  });
});

test("the exact frozen R8 provider adapter may use fetch without reading process environment", async () => {
  await withFixture(async ({ root }) => {
    const operation = ["fet", "ch"].join("");
    const providerRoot = path.join(
      root,
      "packages",
      "prototype-generator",
      "src",
    );
    await fs.mkdir(providerRoot, { recursive: true });
    await fs.writeFile(
      path.join(providerRoot, "openai-compatible.mjs"),
      `export const request = (endpoint, options) => ${operation}(endpoint, options);\n`,
      "utf8",
    );

    const result = runChecker(root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.equal(result.report.ok, true);
  });
});

test("the exact R9 Meshy adapter may use fetch without reading process environment", async () => {
  await withFixture(async ({ root }) => {
    const operation = ["fet", "ch"].join("");
    const providerRoot = path.join(
      root,
      "packages",
      "prototype-asset-pipeline",
      "src",
    );
    await fs.mkdir(providerRoot, { recursive: true });
    await fs.writeFile(
      path.join(providerRoot, "meshy-provider.mjs"),
      `export const request = (endpoint, options) => ${operation}(endpoint, options);\n`,
      "utf8",
    );

    const result = runChecker(root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.equal(result.report.ok, true);
  });
});

test("the exact R10 Marble adapter may use fetch without reading process environment", async () => {
  await withFixture(async ({ root }) => {
    const operation = ["fet", "ch"].join("");
    const providerRoot = path.join(
      root,
      "packages",
      "prototype-environment-pipeline",
      "src",
    );
    await fs.mkdir(providerRoot, { recursive: true });
    await fs.writeFile(
      path.join(providerRoot, "marble-provider.mjs"),
      `export const request = (endpoint, options) => ${operation}(endpoint, options);\n`,
      "utf8",
    );

    const result = runChecker(root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.equal(result.report.ok, true);
  });
});

test("secret scanning permits an indirect credential reference but not embedded values", async () => {
  await withFixture(async ({ root }) => {
    await fs.mkdir(path.join(root, "src"), { recursive: true });
    await fs.writeFile(
      path.join(root, "src", "config.js"),
      [
        "const config = Object.freeze({ credential: readCredential() });",
        "export const options = { apiKey: config.credential };",
        "",
      ].join("\n"),
      "utf8",
    );

    const result = runChecker(root);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.equal(result.report.ok, true);
  });
});

const negativeCases = [
  {
    name: "network call outside the exact provider adapter",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const operation = ["fet", "ch"].join("");
      const sourceRoot = path.join(root, "packages", "prototype-generator", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "helper.mjs"),
        `export const request = (endpoint) => ${operation}(endpoint);\n`,
        "utf8",
      );
    },
  },
  {
    name: "network call in a Meshy helper outside the exact provider adapter",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const operation = ["fet", "ch"].join("");
      const sourceRoot = path.join(root, "packages", "prototype-asset-pipeline", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "helper.mjs"),
        `export const request = (endpoint) => ${operation}(endpoint);\n`,
        "utf8",
      );
    },
  },
  {
    name: "approved provider adapter reads process environment",
    expectedRule: "provider-network-capability-forbidden",
    setup: async ({ root }) => {
      const operation = ["fet", "ch"].join("");
      const processEnvironment = ["process", "env"].join(".");
      const sourceRoot = path.join(root, "packages", "prototype-generator", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "openai-compatible.mjs"),
        `export const request = (endpoint) => ${operation}(endpoint, { headers: ${processEnvironment} });\n`,
        "utf8",
      );
    },
  },
  {
    name: "import escape toward client source",
    expectedRule: "import-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath(parentSegment, parentSegment, "client", "src", "tool.js");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "bad-client.js"),
        `import value from "${escape}";\nexport { value };\n`,
        "utf8",
      );
    },
  },
  {
    name: "import escape toward server",
    expectedRule: "import-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath(parentSegment, parentSegment, "server", "main.js");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "bad-server.js"),
        `export { default } from "${escape}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "external file dependency",
    expectedRule: "dependency-outside-module",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ dependencies: { shared: "file:../../shared" } }),
      );
    },
  },
  {
    name: "package script escaping the module",
    expectedRule: "script-path-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath(parentSegment, parentSegment, "client", "tool.mjs");
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ scripts: { probe: `node ${escape}` } }),
      );
    },
  },
  {
    name: "package script uses node eval",
    expectedRule: "script-inline-command-unverifiable",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ scripts: { probe: 'node -e "process.exit(0)"' } }),
      );
    },
  },
  {
    name: "package script uses PowerShell command text",
    expectedRule: "script-inline-command-unverifiable",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ scripts: { probe: 'pwsh -Command "Get-Location"' } }),
      );
    },
  },
  {
    name: "package script uses cmd command text",
    expectedRule: "script-inline-command-unverifiable",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ scripts: { probe: "cmd /c echo fixture" } }),
      );
    },
  },
  {
    name: "package script uses shell command text",
    expectedRule: "script-inline-command-unverifiable",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ scripts: { probe: 'bash -c "pwd"' } }),
      );
    },
  },
  {
    name: "package script uses command substitution",
    expectedRule: "script-inline-command-unverifiable",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({ scripts: { probe: "echo $(pwd)" } }),
      );
    },
  },
  {
    name: "external symbolic link",
    expectedRule: "external-symlink",
    setup: async ({ temporaryRoot, root, links }) => {
      const outside = path.join(temporaryRoot, "outside");
      const link = path.join(root, "external-link");
      await fs.mkdir(outside, { recursive: true });
      await fs.symlink(
        outside,
        link,
        process.platform === "win32" ? "junction" : "dir",
      );
      links.push(link);
    },
  },
  {
    name: "Windows local absolute path",
    expectedRule: "absolute-local-path",
    setup: async ({ root }) => {
      const separator = String.fromCharCode(92);
      const localPath = ["C:", "fixture", "asset.bin"].join(separator);
      const escaped = localPath.replaceAll(separator, separator.repeat(2));
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "windows-path.js"),
        `export const localPath = "${escaped}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "Windows local absolute path embedded in an argument",
    expectedRule: "absolute-local-path",
    setup: async ({ root }) => {
      const separator = String.fromCharCode(92);
      const localPath = ["C:", "Users", "fixture", "config.json"].join(separator);
      const argument = `--config=${localPath}`.replaceAll(
        separator,
        separator.repeat(2),
      );
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "embedded-windows-path.js"),
        `export const argument = "${argument}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "UNC local absolute path embedded in an argument",
    expectedRule: "absolute-local-path",
    setup: async ({ root }) => {
      const separator = String.fromCharCode(92);
      const uncPath = `${separator.repeat(2)}fixture-host${separator}share${separator}config.json`;
      const argument = `--config=${uncPath}`.replaceAll(
        separator,
        separator.repeat(2),
      );
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "embedded-unc-path.js"),
        `export const argument = "${argument}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "Unix local absolute path",
    expectedRule: "absolute-local-path",
    setup: async ({ root }) => {
      const localPath = ["", "opt", "matrix-oasis", "asset.bin"].join("/");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "unix-path.js"),
        `export const localPath = "${localPath}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "generic relative path literal escape",
    expectedRule: "path-literal-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath(parentSegment, "server", "settings.json");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "path-literal.js"),
        `export const settingsPath = "${escape}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "filesystem call path escape",
    expectedRule: "path-literal-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath(parentSegment, "client", "config.json");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "filesystem.js"),
        `import { readFileSync } from "node:fs";\nreadFileSync("${escape}");\n`,
        "utf8",
      );
    },
  },
  {
    name: "filesystem cwd-relative escape from root source",
    expectedRule: "filesystem-path-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath("client", "config.json");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "cwd-filesystem.js"),
        `import fs from "node:fs";\nfs.readFileSync("${escape}");\n`,
        "utf8",
      );
    },
  },
  {
    name: "static array join parent traversal",
    expectedRule: "path-expression-outside-module",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "array-path.js"),
        `export const escaped = ${staticParentJoinSource("client")};\n`,
        "utf8",
      );
    },
  },
  {
    name: "tsconfig extends escape",
    expectedRule: "tsconfig-path-outside-module",
    setup: async ({ root }) => {
      const escape = escapePath(parentSegment, "client", "tsconfig.json");
      await writeJson(path.join(root, "tsconfig.json"), { extends: escape });
    },
  },
  {
    name: "non-literal dynamic import",
    expectedRule: "dynamic-import-nonliteral",
    setup: async ({ root }) => {
      const operation = ["im", "port"].join("");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "dynamic-import.js"),
        `export const load = (target) => ${operation}(target);\n`,
        "utf8",
      );
    },
  },
  {
    name: "non-literal dynamic require",
    expectedRule: "dynamic-require-nonliteral",
    setup: async ({ root }) => {
      const operation = ["requ", "ire"].join("");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "dynamic-require.cjs"),
        `module.exports = (target) => ${operation}(target);\n`,
        "utf8",
      );
    },
  },
  {
    name: "Creator network call",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const operation = ["fet", "ch"].join("");
      const sourceRoot = path.join(root, "apps", "creator-web", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "network.js"),
        `export const load = () => ${operation}("/api");\n`,
        "utf8",
      );
    },
  },
  {
    name: "root runtime source network call",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const operation = ["fet", "ch"].join("");
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "network.js"),
        `export const load = () => ${operation}("/status");\n`,
        "utf8",
      );
    },
  },
  {
    name: "helper runtime source network module",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "helper"), { recursive: true });
      await fs.writeFile(
        path.join(root, "helper", "transport.mjs"),
        'import https from "node:https";\nexport { https };\n',
        "utf8",
      );
    },
  },
  {
    name: "Creator HTML quoted API source",
    expectedRule: "creator-html-network-forbidden",
    setup: async ({ root }) => {
      const creatorRoot = path.join(root, "apps", "creator-web");
      await fs.mkdir(creatorRoot, { recursive: true });
      await fs.writeFile(
        path.join(creatorRoot, "index.html"),
        '<script src="/api/bootstrap"></script>\n',
        "utf8",
      );
    },
  },
  {
    name: "Creator HTML unquoted protocol-relative link",
    expectedRule: "creator-html-network-forbidden",
    setup: async ({ root }) => {
      const creatorRoot = path.join(root, "apps", "creator-web");
      await fs.mkdir(creatorRoot, { recursive: true });
      await fs.writeFile(
        path.join(creatorRoot, "index.html"),
        '<link href=//cdn.example.invalid/app.css>\n',
        "utf8",
      );
    },
  },
  {
    name: "Creator HTML external form action",
    expectedRule: "creator-html-network-forbidden",
    setup: async ({ root }) => {
      const creatorRoot = path.join(root, "apps", "creator-web");
      await fs.mkdir(creatorRoot, { recursive: true });
      await fs.writeFile(
        path.join(creatorRoot, "index.html"),
        '<form action=https://example.invalid/submit></form>\n',
        "utf8",
      );
    },
  },
  {
    name: "app fetch alias",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const capability = ["fet", "ch"].join("");
      const sourceRoot = path.join(root, "apps", "creator-web", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "network-alias.js"),
        `const request = globalThis["${capability}"];\nexport { request };\n`,
        "utf8",
      );
    },
  },
  {
    name: "package XMLHttpRequest capability",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const capability = ["XML", "HttpRequest"].join("");
      const sourceRoot = path.join(root, "packages", "compiler", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "transport.js"),
        `export const Transport = ${capability};\n`,
        "utf8",
      );
    },
  },
  {
    name: "package Node network module",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const sourceRoot = path.join(root, "packages", "compiler", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      await fs.writeFile(
        path.join(sourceRoot, "socket.js"),
        'import http from "node:http";\nexport { http };\n',
        "utf8",
      );
    },
  },
  {
    name: "package protocol-relative URL",
    expectedRule: "module-network-forbidden",
    setup: async ({ root }) => {
      const sourceRoot = path.join(root, "packages", "compiler", "src");
      await fs.mkdir(sourceRoot, { recursive: true });
      const endpoint = ["", "", "telemetry.example.invalid", "v1"].join("/");
      await fs.writeFile(
        path.join(sourceRoot, "endpoint.js"),
        `export const endpoint = "${endpoint}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "non-smoke script network capability",
    expectedRule: "script-network-forbidden",
    setup: async ({ root }) => {
      const capability = ["fet", "ch"].join("");
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "download.mjs"),
        `${capability}("https://example.invalid/tool");\n`,
        "utf8",
      );
    },
  },
  {
    name: "prototype host outbound request",
    expectedRule: "prototype-host-network-invalid",
    setup: async ({ root }) => {
      const target = path.join(root, "scripts", "lib", "prototype-host-core.mjs");
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, [
        'import { createServer } from "node:http";',
        'export const PROTOTYPE_HOST = "127.0.0.1";',
        "export const PROTOTYPE_HOST_PORT = 43_110;",
        "const server = createServer();",
        "server.listen(PROTOTYPE_HOST_PORT, PROTOTYPE_HOST, () => {});",
        'fetch("https://example.invalid");',
      ].join("\n"), "utf8");
    },
  },
  {
    name: "prototype host wildcard binding",
    expectedRule: "prototype-host-network-invalid",
    setup: async ({ root }) => {
      const target = path.join(root, "scripts", "lib", "prototype-host-core.mjs");
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, [
        'import { createServer } from "node:http";',
        'export const PROTOTYPE_HOST = "0.0.0.0";',
        "export const PROTOTYPE_HOST_PORT = 43_110;",
        "const server = createServer();",
        "server.listen(PROTOTYPE_HOST_PORT, PROTOTYPE_HOST, () => {});",
      ].join("\n"), "utf8");
    },
  },
  {
    name: "smoke script non-loopback host",
    expectedRule: "smoke-host-not-fixed-loopback",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        'const LOOPBACK_HOST = "0.0.0.0";\nexport { LOOPBACK_HOST };\n',
        "utf8",
      );
    },
  },
  {
    name: "smoke target derived from process arguments",
    expectedRule: "smoke-target-not-static",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        validSmokeSource("const requestedHost = process.argv[2];"),
        "utf8",
      );
    },
  },
  {
    name: "smoke target derived from process environment",
    expectedRule: "smoke-target-not-static",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        validSmokeSource("const requestedHost = process.env.SMOKE_HOST;"),
        "utf8",
      );
    },
  },
  {
    name: "smoke imports an additional HTTPS module",
    expectedRule: "smoke-network-module-forbidden",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        `${validSmokeSource()}\nimport https from "node:https";\nvoid https;\n`,
        "utf8",
      );
    },
  },
  {
    name: "smoke opens an outbound net client",
    expectedRule: "smoke-net-client-forbidden",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        validSmokeSource("net.connect(443, LOOPBACK_HOST);"),
        "utf8",
      );
    },
  },
  {
    name: "smoke fetches a non-fixed target",
    expectedRule: "smoke-request-target-not-fixed",
    setup: async ({ root }) => {
      const source = validSmokeSource().replace(
        'fetch(url, { method: "GET" });',
        'const targetUrl = url;\nfetch(targetUrl, { method: "GET" });',
      );
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        source,
        "utf8",
      );
    },
  },
  {
    name: "smoke preview host bypasses the fixed constant",
    expectedRule: "smoke-loopback-binding-invalid",
    setup: async ({ root }) => {
      const source = validSmokeSource().replace(
        "host: LOOPBACK_HOST",
        'host: "localhost"',
      );
      await fs.mkdir(path.join(root, "scripts"), { recursive: true });
      await fs.writeFile(
        path.join(root, "scripts", "smoke-creator.mjs"),
        source,
        "utf8",
      );
    },
  },
  {
    name: "secret-like key",
    expectedRule: "secret-content",
    setup: async ({ root }) => {
      const token = "sk-" + "a".repeat(32);
      await fs.mkdir(path.join(root, "src"), { recursive: true });
      await fs.writeFile(
        path.join(root, "src", "secret.js"),
        `export const credential = "${token}";\n`,
        "utf8",
      );
    },
  },
  {
    name: "unquoted gateway key in README",
    expectedRule: "secret-content",
    setup: async ({ root }) => {
      const keyName = ["LLM", "_GATEWAY", "_KEY"].join("");
      const value = ["r0", "-fixture-", "q".repeat(24)].join("");
      await fs.writeFile(
        path.join(root, "README.md"),
        `${keyName}=${value}\n`,
        "utf8",
      );
    },
  },
  {
    name: "unquoted npm auth token in env example",
    expectedRule: "secret-content",
    setup: async ({ root }) => {
      const keyName = ["_auth", "Token"].join("");
      const value = ["npm", "-fixture-", "z".repeat(24)].join("");
      await fs.writeFile(
        path.join(root, ".env.example"),
        `${keyName}=${value}\n`,
        "utf8",
      );
    },
  },
  {
    name: "environment file",
    expectedRule: "forbidden-file",
    setup: async ({ root }) => {
      await fs.writeFile(path.join(root, ".env"), "INTERNAL_FLAG=fixture\n", "utf8");
    },
  },
  {
    name: "tracked generated directory",
    expectedRule: "tracked-generated-path",
    setup: async ({ root }) => {
      const generatedFile = path.join(root, "dist", "leak.js");
      await fs.mkdir(path.dirname(generatedFile), { recursive: true });
      await fs.writeFile(generatedFile, "export const leak = true;\n", "utf8");
      const add = spawnSync("git", ["add", "-f", "dist/leak.js"], {
        cwd: root,
        encoding: "utf8",
        shell: false,
        windowsHide: true,
      });
      assert.equal(add.status, 0, add.stderr);
    },
  },
  {
    name: "tampered core boundary policy",
    expectedRule: "boundary-policy-invalid",
    setup: async ({ root }) => {
      const policy = boundaryPolicy();
      policy.parentIntegration = "adapter";
      await writeJson(path.join(root, "module-boundary.json"), policy);
    },
  },
  {
    name: "tampered OpenRouter provider boundary policy",
    expectedRule: "boundary-policy-invalid",
    setup: async ({ root }) => {
      const policy = boundaryPolicy();
      policy.prototypeGenerationPolicy.openRouterHost = "router.example.invalid";
      await writeJson(path.join(root, "module-boundary.json"), policy);
    },
  },
  {
    name: "tampered provider schema transform policy",
    expectedRule: "boundary-policy-invalid",
    setup: async ({ root }) => {
      const policy = boundaryPolicy();
      policy.prototypeGenerationPolicy.providerSchemaKeywordTransforms[0].to = "oneOf";
      await writeJson(path.join(root, "module-boundary.json"), policy);
    },
  },
  {
    name: "tampered provider required-property policy",
    expectedRule: "boundary-policy-invalid",
    setup: async ({ root }) => {
      const policy = boundaryPolicy();
      policy.prototypeGenerationPolicy.providerSchemaRequiresAllProperties = false;
      await writeJson(path.join(root, "module-boundary.json"), policy);
    },
  },
  {
    name: "tampered provider definition-flattening policy",
    expectedRule: "boundary-policy-invalid",
    setup: async ({ root }) => {
      const policy = boundaryPolicy();
      policy.prototypeGenerationPolicy.providerSchemaFlattensNestedDefinitions = false;
      await writeJson(path.join(root, "module-boundary.json"), policy);
    },
  },
  {
    name: "tampered Meshy provider endpoint policy",
    expectedRule: "boundary-policy-invalid",
    setup: async ({ root }) => {
      const policy = boundaryPolicy();
      policy.prototypeAssetPolicy.providerEndpoint = [
        "https:",
        "",
        "mesh.example.invalid",
        "v2",
      ].join("/");
      await writeJson(path.join(root, "module-boundary.json"), policy);
    },
  },
  {
    name: "CC-BY exception used by a runtime dependency",
    expectedRule: "dependency-license-exception-scope",
    setup: async ({ root }) => {
      await writeJson(path.join(root, "package-lock.json"), {
        name: "boundary-fixture",
        version: "0.0.0-r1",
        lockfileVersion: 3,
        packages: {
          "": privatePackage(),
          "node_modules/caniuse-lite": {
            version: "1.0.30001807",
            license: "CC-BY-4.0",
          },
        },
      });
    },
  },
  {
    name: "CC-BY exception declared directly without a synchronized lockfile",
    expectedRule: "dependency-license-exception-scope",
    setup: async ({ root }) => {
      await writeJson(
        path.join(root, "package.json"),
        privatePackage({
          devDependencies: { "caniuse-lite": "1.0.30001807" },
        }),
      );
    },
  },
  {
    name: "CC-BY exception used by a direct dependency",
    expectedRule: "dependency-license-exception-scope",
    setup: async ({ root }) => {
      await writeJson(path.join(root, "package-lock.json"), {
        name: "boundary-fixture",
        version: "0.0.0-r1",
        lockfileVersion: 3,
        packages: {
          "": privatePackage({
            devDependencies: { "caniuse-lite": "1.0.30001807" },
          }),
          "node_modules/caniuse-lite": {
            version: "1.0.30001807",
            license: "CC-BY-4.0",
            dev: true,
          },
        },
      });
    },
  },
  {
    name: "Godot project file outside the approved root",
    expectedRule: "godot-artifact-forbidden",
    setup: async ({ root }) => {
      await fs.writeFile(path.join(root, "project.godot"), "[application]\n", "utf8");
    },
  },
  {
    name: "Godot script outside the approved root",
    expectedRule: "godot-artifact-forbidden",
    setup: async ({ root }) => {
      await fs.writeFile(path.join(root, "player.gd"), "extends Node\n", "utf8");
    },
  },
  {
    name: "Godot source identity outside the approved root",
    expectedRule: "godot-artifact-forbidden",
    setup: async ({ root }) => {
      await fs.writeFile(path.join(root, "player.gd.uid"), "uid://fixtureidentity\n", "utf8");
    },
  },
  {
    name: "Godot addons directory outside the approved root",
    expectedRule: "godot-addon-directory-forbidden",
    setup: async ({ root }) => {
      await fs.mkdir(path.join(root, "addons", "fixture"), { recursive: true });
    },
  },
  {
    name: "unapproved addon beside GdUnit4",
    expectedRule: "godot-addon-directory-forbidden",
    setup: async ({ root }) => {
      await fs.mkdir(
        path.join(root, "apps", "runtime-godot", "addons", "unknown-addon"),
        { recursive: true },
      );
    },
  },
  {
    name: "binary artifact forbidden in the active round",
    expectedRule: "binary-artifact-forbidden",
    setup: async ({ root }) => {
      await fs.writeFile(path.join(root, "runtime.dll"), "fixture", "utf8");
    },
  },
  {
    name: "rotated log forbidden in the active round",
    expectedRule: "rotated-log-forbidden",
    setup: async ({ root }) => {
      await fs.writeFile(path.join(root, "verify.log.1"), "fixture\n", "utf8");
    },
  },
];

test("negative fixtures fail with relative diagnostics", async (t) => {
  for (const fixtureCase of negativeCases) {
    await t.test(fixtureCase.name, async () => {
      await withFixture(async (fixture) => {
        await fixtureCase.setup(fixture);
        const result = runChecker(fixture.root);

        assert.equal(result.status, 1, result.stderr || result.stdout);
        assert.equal(result.report.ok, false);
        assert.equal(
          result.report.violations.some(
            (violation) => violation.rule === fixtureCase.expectedRule,
          ),
          true,
          JSON.stringify(result.report.violations),
        );
        for (const violation of result.report.violations) {
          assert.equal(path.isAbsolute(violation.path), false);
          assert.equal(violation.path.includes(fixture.temporaryRoot), false);
        }
        assert.equal(JSON.stringify(result.report).includes(fixture.temporaryRoot), false);
      });
    });
  }
});

test("a non-Git module fails closed as an operational error", async () => {
  await withFixture(async (fixture) => {
    await fs.rm(path.join(fixture.root, ".git"), { recursive: true, force: true });
    const result = runChecker(fixture.root);

    assert.equal(result.status, 2);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /^BOUNDARY_CHECK_OPERATIONAL_ERROR\s*$/);
    assert.equal(result.stderr.includes(fixture.temporaryRoot), false);
  });
});

test("a Git ls-files failure fails closed as an operational error", async () => {
  await withFixture(async (fixture) => {
    await fs.writeFile(path.join(fixture.root, ".git", "index"), "invalid-index", "utf8");
    const result = runChecker(fixture.root);

    assert.equal(result.status, 2);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /^BOUNDARY_CHECK_OPERATIONAL_ERROR\s*$/);
    assert.equal(result.stderr.includes(fixture.temporaryRoot), false);
  });
});
