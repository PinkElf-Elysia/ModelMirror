import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { isDeepStrictEqual } from "node:util";
import {
  ACTIVE_ROUND,
  ACTIVE_ROUND_BASELINE_SHA,
} from "./scope-policy.mjs";

const DEPENDENCY_GROUPS = [
  "dependencies",
  "devDependencies",
  "peerDependencies",
  "optionalDependencies",
];
const EXECUTABLE_EXTENSIONS = new Set([
  ".cjs",
  ".css",
  ".html",
  ".js",
  ".jsx",
  ".json",
  ".mjs",
  ".ts",
  ".tsx",
]);
const EXECUTABLE_NAMES = new Set([".npmrc", ".nvmrc"]);
const TEXT_EXTENSIONS = new Set([
  ...EXECUTABLE_EXTENSIONS,
  ".cfg",
  ".gd",
  ".gdshader",
  ".godot",
  ".md",
  ".tscn",
  ".tres",
  ".toml",
  ".txt",
  ".yaml",
  ".yml",
]);
const TEXT_NAMES = new Set([
  ...EXECUTABLE_NAMES,
  ".gitignore",
  "LICENSE",
  "NOTICE",
  "README",
]);
const EXACT_VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const LOCAL_ABSOLUTE_PATH = /(?:^|[\s=,;(])(?:[A-Za-z]:[\\/]|\\\\(?:\?\\|[^\\/\s"'=]+[\\/])|\/(?:Users|home|tmp|var|opt|etc|private)\/)/;
const FILESYSTEM_PATH_METHODS = [
  "access",
  "accessSync",
  "appendFile",
  "appendFileSync",
  "chmod",
  "chmodSync",
  "chown",
  "chownSync",
  "copyFile",
  "copyFileSync",
  "cp",
  "cpSync",
  "createReadStream",
  "createWriteStream",
  "existsSync",
  "lstat",
  "lstatSync",
  "mkdir",
  "mkdirSync",
  "open",
  "openSync",
  "opendir",
  "opendirSync",
  "readFile",
  "readFileSync",
  "readdir",
  "readdirSync",
  "readlink",
  "readlinkSync",
  "realpath",
  "realpathSync",
  "rename",
  "renameSync",
  "rm",
  "rmSync",
  "rmdir",
  "rmdirSync",
  "stat",
  "statSync",
  "symlink",
  "symlinkSync",
  "truncate",
  "truncateSync",
  "unlink",
  "unlinkSync",
  "utimes",
  "utimesSync",
  "watch",
  "watchFile",
  "writeFile",
  "writeFileSync",
];
const RUNTIME_SOURCE_EXTENSIONS = new Set([
  ".cjs",
  ".css",
  ".html",
  ".js",
  ".jsx",
  ".mjs",
  ".ts",
  ".tsx",
]);
const NETWORK_GLOBAL_NAMES = [
  ["fet", "ch"].join(""),
  ["XML", "HttpRequest"].join(""),
  ["Web", "Socket"].join(""),
  ["Event", "Source"].join(""),
  ["send", "Beacon"].join(""),
];
const FETCH_GLOBAL_NAME = NETWORK_GLOBAL_NAMES[0];
const NETWORK_MODULES = new Set([
  "http",
  "http2",
  "https",
  "net",
  "tls",
  "dgram",
  "dns",
  "dns/promises",
  "node:http",
  "node:http2",
  "node:https",
  "node:net",
  "node:tls",
  "node:dgram",
  "node:dns",
  "node:dns/promises",
  "axios",
  "got",
  "ky",
  "superagent",
  "undici",
]);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);
const APPROVED_PROVIDER_NETWORK_SOURCE =
  "packages/prototype-generator/src/openai-compatible.mjs";
const STATIC_SECRET_PATTERNS = [
  /-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----/,
  /\bsk-[A-Za-z0-9_-]{20,}\b/,
  /\bgh[pousr]_[A-Za-z0-9]{20,}\b/,
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/,
];
const ASSIGNED_SECRET = /\b(?:OPENROUTER_API_KEY|LLM_GATEWAY_KEY|DIFY_API_KEY|GITHUB_TOKEN|NPM_TOKEN|_authToken|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|secret)\s*[:=]\s*(?:"([^"]+)"|'([^']+)'|([^\s#;,]+))/gi;
const REQUIRED_POLICY_VALUES = [
  [["schemaVersion"], 8],
  [["moduleId"], "matrix-oasis-engine"],
  [["moduleRoot"], "."],
  [["moduleRootResolution"], "directory-containing-module-boundary"],
  [["activeRound"], ACTIVE_ROUND],
  [["activeRoundBaselineSha"], ACTIVE_ROUND_BASELINE_SHA],
  [["parentRepositoryPrefix"], "experiments/matrix-oasis-engine/"],
  [["parentIntegration"], "none"],
  [["allowedParentInteractions"], []],
  [["networkPolicy", "creatorSource"], "none"],
  [["networkPolicy", "godotFirstPartySource"], "none"],
  [["networkPolicy", "verificationScripts"], "loopback-only"],
  [["networkPolicy", "providerCalls"], "openai-compatible-adapter-only"],
  [["networkPolicy", "splatQualification"], "source-checkout-and-loopback-disposable-only"],
  [["runtimeArtifactInputPolicy", "mode"], "paired-local-files-only"],
  [["runtimeArtifactInputPolicy", "runtimeMaxBytes"], 16 * 1024 * 1024],
  [["runtimeArtifactInputPolicy", "receiptMaxBytes"], 16 * 1024],
  [
    ["runtimeArtifactInputPolicy", "canonicalization"],
    "matrix-oasis.canonical-json/1",
  ],
  [["runtimeArtifactInputPolicy", "readOnly"], true],
  [["runtimeArtifactInputPolicy", "generatedArtifactsTracked"], false],
  [
    ["runtimeArtifactInputPolicy", "isolatedUtf16Surrogates"],
    "reject-with-static-diagnostic",
  ],
  [["scenePackInputPolicy", "mode"], "local-manifest-and-glb-only"],
  [["scenePackInputPolicy", "manifestMaxBytes"], 256 * 1024],
  [["scenePackInputPolicy", "assetMaxBytes"], 32 * 1024 * 1024],
  [["scenePackInputPolicy", "totalAssetMaxBytes"], 128 * 1024 * 1024],
  [["scenePackInputPolicy", "assetMaxCount"], 16],
  [["scenePackInputPolicy", "placementMaxCount"], 128],
  [["scenePackInputPolicy", "nodeBindingMaxCount"], 4096],
  [["scenePackInputPolicy", "format"], "matrix-oasis.scene-pack"],
  [["scenePackInputPolicy", "formatVersion"], "0.1.0"],
  [["scenePackInputPolicy", "canonicalization"], "matrix-oasis.canonical-json/1"],
  [["scenePackInputPolicy", "allowedAssetFormats"], ["glb"]],
  [["scenePackInputPolicy", "readOnly"], true],
  [["scenePackInputPolicy", "networkAllowed"], false],
  [["scenePackInputPolicy", "providerCallsAllowed"], false],
  [["prototypeGenerationPolicy", "inputModes", 0], "text"],
  [["prototypeGenerationPolicy", "promptMaxBytes"], 32768],
  [["prototypeGenerationPolicy", "responseMaxBytes"], 1048576],
  [["prototypeGenerationPolicy", "maxRequests"], 3],
  [["prototypeGenerationPolicy", "maxRepairAttempts"], 2],
  [["prototypeGenerationPolicy", "endpointPath"], "/v1/chat/completions"],
  [["prototypeGenerationPolicy", "networkSource"], "packages/prototype-generator/src/openai-compatible.mjs"],
  [["prototypeGenerationPolicy", "creatorNetworkAllowed"], false],
  [["prototypeGenerationPolicy", "godotNetworkAllowed"], false],
  [["prototypeGenerationPolicy", "imageInputAllowed"], false],
  [["prototypeGenerationPolicy", "assetProviderCallsAllowed"], false],
  [["prototypeGenerationPolicy", "marbleCallsAllowed"], false],
  [["prototypeGenerationPolicy", "meshyCallsAllowed"], false],
  [["prototypeGenerationPolicy", "trackedGeneratedArtifactsAllowed"], false],
  [["scenePackInputPolicy", "symlinksAllowed"], false],
  [
    ["forbiddenParentRoots"],
    ["client", "server", ".github", "docker-compose.yml", "Dockerfile", "node_modules"],
  ],
  [
    ["forbiddenParentResources"],
    [
      "source",
      "environment-variables",
      "database",
      "docker",
      "routes",
      "assets",
      "build-output",
    ],
  ],
  [["dependencyPolicy", "moduleLocalWorkspacesOnly"], true],
  [["dependencyPolicy", "forbidExternalFileLinks"], true],
  [["dependencyPolicy", "exactVersionsRequired"], true],
  [["dependencyPolicy", "forbiddenExternalProtocols"], ["file:", "link:"]],
  [["pathPolicy", "forbidExternalSymlinks"], true],
  [["pathPolicy", "forbidAbsolutePaths"], true],
  [["pathPolicy", "forbidParentTraversal"], true],
  [["pathPolicy", "forbidResolvedPathsOutsideModule"], true],
  [["toolchain", "requiredForActiveRound", "node"], "24.x"],
  [["toolchain", "requiredForActiveRound", "npm"], "11.x"],
  [["toolchain", "requiredForActiveRound", "git"], "available"],
  [["toolchain", "requiredForActiveRound", "godot"], "4.6.3"],
  [
    ["generatedPaths"],
    [
      "node_modules",
      "dist",
      "coverage",
      ".vite",
      ".godot",
      "exports",
      "logs",
      "test-reports",
      "movie-captures",
    ],
  ],
  [
    ["forbiddenTrackedFileNames"],
    [".env", ".env.local", ".env.development", ".env.production"],
  ],
  [
    ["forbiddenTrackedExtensions"],
    [
      ".log",
      ".pem",
      ".key",
      ".p12",
      ".pfx",
      ".pck",
      ".scn",
      ".res",
      ".gdextension",
      ".gdnlib",
      ".gdns",
      ".import",
      ".exe",
      ".dll",
      ".so",
      ".dylib",
      ".bin",
    ],
  ],
  [["artifactRestrictions", "godotArtifactsForbidden"], false],
  [["artifactRestrictions", "allowedGodotRoot"], "apps/runtime-godot"],
  [
    ["artifactRestrictions", "allowedAddonRoots"],
    ["apps/runtime-godot/addons/gdUnit4"],
  ],
  [["artifactRestrictions", "allowedGodotFileNames"], ["project.godot"]],
  [
    ["artifactRestrictions", "allowedFirstPartyGodotExtensions"],
    [".gd", ".gdshader", ".tscn", ".tres", ".uid"],
  ],
  [["artifactRestrictions", "allowedSceneAssetRoot"], "examples/scene-bundles/kenney-prototype/assets"],
  [["artifactRestrictions", "allowedSceneAssetExtensions"], [".glb"]],
  [["artifactRestrictions", "restrictedAddonDirectoryName"], "addons"],
  [
    ["artifactRestrictions", "forbiddenGodotExtensions"],
    [
      ".pck",
      ".scn",
      ".res",
      ".gdextension",
      ".gdnlib",
      ".gdns",
      ".import",
    ],
  ],
  [
    ["artifactRestrictions", "forbiddenBinaryExtensions"],
    [".exe", ".dll", ".so", ".dylib", ".bin"],
  ],
  [["artifactRestrictions", "rotatedLogsForbidden"], true],
  [["thirdPartyPolicy", "vendorManifest"], "third-party/gdunit4.lock.json"],
  [["thirdPartyPolicy", "referenceManifest"], "third-party/godot-demo-projects/reference.lock.json"],
  [["thirdPartyPolicy", "sceneAssetManifest"], "third-party/kenney-prototype-kit/asset.lock.json"],
  [
    ["thirdPartyPolicy", "allowedVendoredRoots"],
    ["apps/runtime-godot/addons/gdUnit4", "examples/scene-bundles/kenney-prototype/assets"],
  ],
  [
    ["thirdPartyPolicy", "allowedReferenceRoots"],
    ["third-party/godot-demo-projects", "third-party/kenney-prototype-kit"],
  ],
  [["thirdPartyPolicy", "modificationsRequireHumanApproval"], true],
  [["licensePolicy", "moduleLicense"], "UNLICENSED"],
  [
    ["licensePolicy", "allowedDependencyLicenses"],
    ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"],
  ],
  [["licensePolicy", "approvedAssetLicenses"], ["CC0-1.0"]],
  [
    ["licensePolicy", "approvedDependencyLicenseExceptions"],
    [
      {
        package: "caniuse-lite",
        version: "1.0.30001807",
        license: "CC-BY-4.0",
        scope: "transitive-development-dependency",
        approvedOn: "2026-08-06",
        approvalRecord: "user-approved-during-r0",
        compliance:
          "Retain upstream attribution and license notice when distributing dependency materials.",
      },
    ],
  ],
  [["licensePolicy", "otherLicensesRequireHumanApproval"], true],
];

function toPosix(value) {
  return value.split(path.sep).join("/");
}

export function isWithin(root, target) {
  const relative = path.relative(root, target);
  return (
    relative === "" ||
    (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))
  );
}

function relativePath(root, target) {
  const relative = toPosix(path.relative(root, target));
  return relative || ".";
}

function addViolation(violations, rule, relative, message) {
  const normalized = toPosix(relative).replace(/^\.\//, "") || ".";
  const key = `${rule}\0${normalized}\0${message}`;
  if (!violations.some((violation) => violation.key === key)) {
    violations.push({ key, rule, path: normalized, message });
  }
}

function isAbsoluteOnAnyPlatform(value) {
  return path.isAbsolute(value) || path.win32.isAbsolute(value) || path.posix.isAbsolute(value);
}

function valueAt(object, segments) {
  let current = object;
  for (const segment of segments) {
    if (!current || typeof current !== "object" || !(segment in current)) {
      return undefined;
    }
    current = current[segment];
  }
  return current;
}

function validatePolicy(policy, violations) {
  for (const [segments, expected] of REQUIRED_POLICY_VALUES) {
    if (!isDeepStrictEqual(valueAt(policy, segments), expected)) {
      addViolation(
        violations,
        "boundary-policy-invalid",
        "module-boundary.json",
        `Boundary policy field ${segments.join(".")} must retain its approved active-round value.`,
      );
    }
  }
}

function extractStringLiterals(content) {
  const pattern = /"((?:\\.|[^"\\\r\n])*)"|'((?:\\.|[^'\\\r\n])*)'|`((?:\\.|[^`\\\r\n])*)`/g;
  const literals = [];
  for (const match of content.matchAll(pattern)) {
    literals.push({
      raw: match[0],
      value: (match[1] ?? match[2] ?? match[3] ?? "")
        .replaceAll("\\\\", "\\")
        .replaceAll("\\/", "/"),
    });
  }
  return literals;
}

function containsLocalAbsolutePath(content) {
  return extractStringLiterals(content).some(({ value }) => LOCAL_ABSOLUTE_PATH.test(value));
}

function resolveReference(moduleRoot, baseDirectory, reference) {
  if (isAbsoluteOnAnyPlatform(reference)) {
    return { absolute: true, outside: true };
  }

  const target = path.resolve(baseDirectory, reference);
  return { absolute: false, outside: !isWithin(moduleRoot, target) };
}

function checkPathReference({
  moduleRoot,
  baseDirectory,
  reference,
  relative,
  violations,
  rule,
}) {
  const result = resolveReference(moduleRoot, baseDirectory, reference);
  if (result.absolute || result.outside) {
    addViolation(
      violations,
      rule,
      relative,
      "Path reference resolves outside the module boundary.",
    );
  }
}

function dependencyTarget(specifier) {
  const match = /^(?:file|link):(.+)$/.exec(specifier);
  return match?.[1] ?? null;
}

function workspaceTarget(specifier) {
  const match = /^workspace:(.+)$/.exec(specifier);
  if (!match || ["*", "^", "~"].includes(match[1])) {
    return null;
  }
  return /[\\/]|^\./.test(match[1]) ? match[1] : null;
}

function looksLikePathReference(value, includeAbsolute = false) {
  return (
    value.startsWith("./") ||
    value.startsWith("../") ||
    value.startsWith(".\\") ||
    value.startsWith("..\\") ||
    /(^|[\\/])\.\.([\\/]|$)/.test(value) ||
    (includeAbsolute && isAbsoluteOnAnyPlatform(value))
  );
}

function checkCommandPaths(moduleRoot, baseDirectory, relative, command, violations) {
  const tokens = command.match(/"[^"]*"|'[^']*'|[^\s;&|]+/g) ?? [];
  for (const rawToken of tokens) {
    const unquoted = rawToken.replace(/^(?:"([\s\S]*)"|'([\s\S]*)')$/, "$1$2");
    const candidate = unquoted.includes("=")
      ? unquoted.slice(unquoted.indexOf("=") + 1)
      : unquoted;
    if (looksLikePathReference(candidate, true)) {
      checkPathReference({
        moduleRoot,
        baseDirectory,
        reference: candidate,
        relative,
        violations,
        rule: "script-path-outside-module",
      });
    }
  }
}

function checkUnverifiablePackageScript(relative, scriptName, command, violations) {
  const forbiddenForms = [
    /(?:^|[\s;&|])node(?:\.exe)?\s+(?:-e|--eval)(?:\s|=|$)/i,
    /(?:^|[\s;&|])(?:powershell|pwsh)(?:\.exe)?\s+[^\r\n]*?(?:-command)(?:\s|:|$)/i,
    /(?:^|[\s;&|])cmd(?:\.exe)?\s+\/c(?:\s|$)/i,
    /(?:^|[\s;&|])(?:sh|bash)(?:\.exe)?\s+-c(?:\s|$)/i,
    /\$\(/,
    /`/,
  ];
  if (forbiddenForms.some((pattern) => pattern.test(command))) {
    addViolation(
      violations,
      "script-inline-command-unverifiable",
      relative,
      `Package script ${scriptName} contains an inline command that cannot be statically proven module-local.`,
    );
  }
}

function checkDirectExceptionScope(
  relative,
  dependencyName,
  specifier,
  policy,
  violations,
) {
  const exception = (policy.licensePolicy.approvedDependencyLicenseExceptions ?? []).find(
    (entry) => entry.package === dependencyName && entry.version === specifier,
  );
  if (exception?.scope === "transitive-development-dependency") {
    addViolation(
      violations,
      "dependency-license-exception-scope",
      relative,
      `Dependency ${dependencyName}@${specifier} is approved only as a transitive development dependency.`,
    );
  }
}

function checkManifest(moduleRoot, absolute, relative, content, policy, violations) {
  let manifest;
  try {
    manifest = JSON.parse(content);
  } catch {
    addViolation(violations, "invalid-manifest", relative, "package.json is not valid JSON.");
    return;
  }

  if (manifest.private !== true) {
    addViolation(violations, "package-not-private", relative, "Module packages must be private.");
  }
  if (manifest.license !== policy.licensePolicy.moduleLicense) {
    addViolation(
      violations,
      "package-license",
      relative,
      "Module package license does not match the boundary policy.",
    );
  }

  const baseDirectory = path.dirname(absolute);
  const workspaces = Array.isArray(manifest.workspaces)
    ? manifest.workspaces
    : manifest.workspaces?.packages ?? [];
  for (const pattern of workspaces) {
    if (typeof pattern !== "string") {
      addViolation(violations, "workspace-path", relative, "Workspace patterns must be strings.");
      continue;
    }
    const stablePrefix = pattern.split(/[?*[{]/, 1)[0] || ".";
    checkPathReference({
      moduleRoot,
      baseDirectory,
      reference: stablePrefix,
      relative,
      violations,
      rule: "workspace-path",
    });
  }

  for (const [scriptName, command] of Object.entries(manifest.scripts ?? {})) {
    if (typeof command !== "string") {
      addViolation(
        violations,
        "invalid-package-script",
        relative,
        `Package script ${scriptName} must be a string.`,
      );
      continue;
    }
    checkUnverifiablePackageScript(relative, scriptName, command, violations);
    checkCommandPaths(moduleRoot, baseDirectory, relative, command, violations);
  }

  for (const group of DEPENDENCY_GROUPS) {
    for (const [name, specifier] of Object.entries(manifest[group] ?? {})) {
      if (typeof specifier !== "string") {
        addViolation(
          violations,
          "dependency-specifier",
          relative,
          `Dependency ${name} must use a string specifier.`,
        );
        continue;
      }

      checkDirectExceptionScope(relative, name, specifier, policy, violations);

      const pathTarget = dependencyTarget(specifier) ?? workspaceTarget(specifier);
      if (pathTarget) {
        checkPathReference({
          moduleRoot,
          baseDirectory,
          reference: pathTarget,
          relative,
          violations,
          rule: "dependency-outside-module",
        });
        continue;
      }

      if (
        policy.dependencyPolicy.exactVersionsRequired &&
        !specifier.startsWith("workspace:") &&
        !EXACT_VERSION.test(specifier)
      ) {
        addViolation(
          violations,
          "dependency-version-not-exact",
          relative,
          `Dependency ${name} must use an exact version.`,
        );
      }
    }
  }
}

function packageNameFromLockPath(lockPath) {
  const normalized = lockPath.replaceAll("\\", "/");
  const marker = "node_modules/";
  const index = normalized.lastIndexOf(marker);
  return index >= 0 ? normalized.slice(index + marker.length) : normalized;
}

function inspectProtocolValues(value, moduleRoot, baseDirectory, relative, violations) {
  if (typeof value === "string") {
    const target = dependencyTarget(value) ?? workspaceTarget(value);
    if (target) {
      checkPathReference({
        moduleRoot,
        baseDirectory,
        reference: target,
        relative,
        violations,
        rule: "lock-path-outside-module",
      });
    }
    return;
  }
  if (!value || typeof value !== "object") {
    return;
  }
  for (const child of Object.values(value)) {
    inspectProtocolValues(child, moduleRoot, baseDirectory, relative, violations);
  }
}

function collectDirectDependencyNames(lockfile) {
  const direct = new Set();
  for (const [lockPath, metadata] of Object.entries(lockfile.packages ?? {})) {
    if (lockPath.includes("node_modules/") || !metadata || typeof metadata !== "object") {
      continue;
    }
    for (const group of DEPENDENCY_GROUPS) {
      for (const name of Object.keys(metadata[group] ?? {})) {
        direct.add(name);
      }
    }
  }
  return direct;
}

function checkLockfile(moduleRoot, absolute, relative, content, policy, violations) {
  let lockfile;
  try {
    lockfile = JSON.parse(content);
  } catch {
    addViolation(violations, "invalid-lockfile", relative, "package-lock.json is not valid JSON.");
    return;
  }

  inspectProtocolValues(lockfile, moduleRoot, path.dirname(absolute), relative, violations);

  const directDependencies = collectDirectDependencyNames(lockfile);
  const allowedLicenses = new Set(policy.licensePolicy.allowedDependencyLicenses);
  const exceptions = policy.licensePolicy.approvedDependencyLicenseExceptions ?? [];
  for (const [lockPath, metadata] of Object.entries(lockfile.packages ?? {})) {
    if (!lockPath.includes("node_modules/") || metadata.link === true) {
      continue;
    }
    const packageName = packageNameFromLockPath(lockPath);
    const license = metadata.license ?? "UNKNOWN";
    if (allowedLicenses.has(license)) {
      continue;
    }

    const exception = exceptions.find(
      (entry) =>
        entry.package === packageName &&
        entry.version === metadata.version &&
        entry.license === license,
    );
    if (!exception) {
      addViolation(
        violations,
        "dependency-license-not-approved",
        relative,
        `Dependency ${packageName}@${metadata.version ?? "unknown"} requires license approval.`,
      );
      continue;
    }

    if (
      exception.scope !== "transitive-development-dependency" ||
      metadata.dev !== true ||
      directDependencies.has(packageName)
    ) {
      addViolation(
        violations,
        "dependency-license-exception-scope",
        relative,
        `Dependency ${packageName}@${metadata.version ?? "unknown"} does not satisfy its approved transitive-development scope.`,
      );
    }
  }
}

function extractModuleSpecifiers(content, extension) {
  const patterns = [
    /(?:import|export)\s+(?:[^"']*?\s+from\s*)?["']([^"']+)["']/g,
    /import\s*\(\s*["']([^"']+)["']\s*\)/g,
    /require\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  if (extension === ".css") {
    patterns.push(/@import\s+(?:url\()?\s*["']?([^"')\s]+)["']?\s*\)?/g);
    patterns.push(/url\(\s*["']?([^"')]+)["']?\s*\)/g);
  }

  const specifiers = [];
  for (const pattern of patterns) {
    for (const match of content.matchAll(pattern)) {
      specifiers.push(match[1]);
    }
  }
  return specifiers;
}

function checkDynamicModuleLoads(relative, content, violations) {
  const patterns = [
    ["dynamic-import-nonliteral", /\bimport\s*\(([^)]*)\)/g],
    ["dynamic-require-nonliteral", /\brequire\s*\(([^)]*)\)/g],
  ];
  for (const [rule, pattern] of patterns) {
    for (const match of content.matchAll(pattern)) {
      const argument = match[1].trim();
      if (!/^(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')$/.test(argument)) {
        addViolation(
          violations,
          rule,
          relative,
          "Dynamic module loading must use a single static string literal.",
        );
      }
    }
  }
}

function checkFilesystemPathArguments(moduleRoot, relative, content, violations) {
  const methodAlternation = FILESYSTEM_PATH_METHODS.join("|");
  const literal = String.raw`(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|\x60(?:\\.|[^\x60\\\r\n])*\x60)`;
  const callPattern = new RegExp(
    String.raw`\b(?:(?:fs|fsp)\s*\.\s*)?(?:${methodAlternation})\s*\(\s*(${literal})`,
    "g",
  );
  for (const match of content.matchAll(callPattern)) {
    const [{ value } = { value: "" }] = extractStringLiterals(match[1]);
    if (!value || value.includes("${")) {
      continue;
    }
    checkPathReference({
      moduleRoot,
      baseDirectory: moduleRoot,
      reference: value,
      relative,
      violations,
      rule: "filesystem-path-outside-module",
    });
  }
}

function checkStaticArrayPathComposition(moduleRoot, relative, content, violations) {
  const arrayJoinPattern = /\[((?:\s*(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|`(?:\\.|[^`\\\r\n])*`)\s*,?)+)\]\s*\.\s*join\s*\(\s*(["'])([\\/])\2\s*\)/g;
  for (const match of content.matchAll(arrayJoinPattern)) {
    const segments = extractStringLiterals(match[1]).map(({ value }) => value);
    if (!segments.includes("..")) {
      continue;
    }
    const reference = segments.join(match[3]);
    checkPathReference({
      moduleRoot,
      baseDirectory: moduleRoot,
      reference,
      relative,
      violations,
      rule: "path-expression-outside-module",
    });
  }
}

function checkGenericPathLiterals(moduleRoot, absolute, relative, content, violations) {
  for (const { value } of extractStringLiterals(content)) {
    if (!value.includes("${") && looksLikePathReference(value)) {
      checkPathReference({
        moduleRoot,
        baseDirectory: path.dirname(absolute),
        reference: value.split(/[?#]/, 1)[0],
        relative,
        violations,
        rule: "path-literal-outside-module",
      });
    }
  }

  checkFilesystemPathArguments(moduleRoot, relative, content, violations);
  checkStaticArrayPathComposition(moduleRoot, relative, content, violations);

  const composedPath = /\bpath\.(?:join|resolve)\s*\(([^()]*)\)/g;
  for (const match of content.matchAll(composedPath)) {
    const literals = extractStringLiterals(match[1]).map(({ value }) => value);
    if (!literals.some((value) => /(^|[\\/])\.\.([\\/]|$)/.test(value))) {
      continue;
    }
    const onlyLiterals = match[1]
      .replace(/"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|`(?:\\.|[^`\\\r\n])*`/g, "")
      .replace(/[\s,]/g, "") === "";
    if (onlyLiterals) {
      checkPathReference({
        moduleRoot,
        baseDirectory: path.dirname(absolute),
        reference: path.join(...literals),
        relative,
        violations,
        rule: "path-expression-outside-module",
      });
    } else {
      addViolation(
        violations,
        "path-expression-unverifiable",
        relative,
        "Path composition containing parent traversal cannot be proven module-local.",
      );
    }
  }
}

function stableGlobPrefix(value) {
  return value.split(/[?*[{]/, 1)[0] || ".";
}

function checkTsconfig(moduleRoot, absolute, relative, content, violations) {
  let config;
  try {
    config = JSON.parse(content);
  } catch {
    addViolation(violations, "invalid-tsconfig", relative, "tsconfig must be valid JSON.");
    return;
  }
  const baseDirectory = path.dirname(absolute);
  const references = [];
  if (typeof config.extends === "string" && looksLikePathReference(config.extends, true)) {
    references.push(config.extends);
  }
  for (const field of ["files", "include", "exclude"]) {
    for (const value of config[field] ?? []) {
      if (typeof value === "string") {
        references.push(stableGlobPrefix(value));
      }
    }
  }
  for (const field of ["baseUrl", "rootDir", "outDir"]) {
    const value = config.compilerOptions?.[field];
    if (typeof value === "string") {
      references.push(value);
    }
  }
  for (const value of config.compilerOptions?.typeRoots ?? []) {
    if (typeof value === "string") {
      references.push(value);
    }
  }
  for (const values of Object.values(config.compilerOptions?.paths ?? {})) {
    for (const value of Array.isArray(values) ? values : []) {
      if (typeof value === "string") {
        references.push(stableGlobPrefix(value));
      }
    }
  }
  for (const reference of config.references ?? []) {
    if (typeof reference?.path === "string") {
      references.push(reference.path);
    }
  }
  for (const reference of references) {
    checkPathReference({
      moduleRoot,
      baseDirectory,
      reference,
      relative,
      violations,
      rule: "tsconfig-path-outside-module",
    });
  }
}

function hasExternalOrProtocolRelativeUrl(content) {
  return (
    /\b(?:https?|wss?):\/\//i.test(content) ||
    /["'`](?:\/\/)[A-Za-z0-9.-]+(?:[:/]|["'`])/i.test(content)
  );
}

function usesNetworkModule(specifiers) {
  return specifiers.some((specifier) => NETWORK_MODULES.has(specifier));
}

function checkRuntimeNetwork(relative, content, specifiers, violations) {
  if (relative === APPROVED_PROVIDER_NETWORK_SOURCE) {
    const forbiddenCapability =
      NETWORK_GLOBAL_NAMES
        .filter((name) => name !== FETCH_GLOBAL_NAME)
        .some((name) => new RegExp(`\\b${name}\\b`).test(content)) ||
      usesNetworkModule(specifiers) ||
      /\bprocess\s*\.\s*env\b/.test(content);
    if (forbiddenCapability) {
      addViolation(
        violations,
        "provider-network-capability-forbidden",
        relative,
        "The approved provider adapter may use only the approved request helper and may not read process environment values.",
      );
    }
    return;
  }

  const forbiddenCapability =
    NETWORK_GLOBAL_NAMES.some((name) =>
      new RegExp(`\\b${name}\\b`).test(content),
    ) ||
    usesNetworkModule(specifiers) ||
    hasExternalOrProtocolRelativeUrl(content);
  if (forbiddenCapability) {
    addViolation(
      violations,
      "module-network-forbidden",
      relative,
      "Runtime sources must not contain network capabilities or URLs.",
    );
  }
}

function checkCreatorHtmlNetwork(relative, content, violations) {
  const attributePattern = /\b(?:src|href|action)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+))/gi;
  for (const match of content.matchAll(attributePattern)) {
    const target = (match[1] ?? match[2] ?? match[3] ?? "").trim();
    if (
      /^\/api(?:[/?#]|$)/i.test(target) ||
      /^\/\/[A-Za-z0-9.-]+(?:[:/]|$)/i.test(target) ||
      /^https?:\/\//i.test(target)
    ) {
      addViolation(
        violations,
        "creator-html-network-forbidden",
        relative,
        "Creator HTML must not point src, href, or action attributes at an API or network host.",
      );
    }
  }
}

function checkSmokeNetwork(relative, content, specifiers, violations) {
  const hostMatch = /\bconst\s+LOOPBACK_HOST\s*=\s*["']([^"']+)["']\s*;/.exec(content);
  if (!hostMatch || !LOOPBACK_HOSTS.has(hostMatch[1])) {
    addViolation(
      violations,
      "smoke-host-not-fixed-loopback",
      relative,
      "Creator smoke must declare a fixed loopback host.",
    );
  }

  const fixedUrl = /\bconst\s+url\s*=\s*`http:\/\/\$\{LOOPBACK_HOST\}:\$\{port\}\/`\s*;/.test(
    content,
  );
  const fixedPreviewHost = /\bpreview\s*:\s*\{[\s\S]*?\bhost\s*:\s*LOOPBACK_HOST\b/.test(
    content,
  );
  const listenCalls = [...content.matchAll(/\.\s*listen\s*\(/g)];
  const fixedListen =
    listenCalls.length > 0 &&
    listenCalls.every((match) =>
      /^\.\s*listen\s*\(\s*0\s*,\s*LOOPBACK_HOST\s*,/.test(
        content.slice(match.index),
      ),
    );
  const hostProperties = [...content.matchAll(/\bhost\s*:\s*([^,}\r\n]+)/g)];
  const onlyFixedHostProperties =
    hostProperties.length > 0 &&
    hostProperties.every((match) => match[1].trim() === "LOOPBACK_HOST");
  if (!fixedUrl || !fixedPreviewHost || !fixedListen || !onlyFixedHostProperties) {
    addViolation(
      violations,
      "smoke-loopback-binding-invalid",
      relative,
      "Creator smoke must use LOOPBACK_HOST for its URL, listener, and preview host.",
    );
  }

  if (/\bprocess\s*\.\s*(?:argv|env)\b/.test(content)) {
    addViolation(
      violations,
      "smoke-target-not-static",
      relative,
      "Creator smoke targets must not be derived from process arguments or environment variables.",
    );
  }

  const allowedSmokeModules = new Set(["net", "node:net"]);
  if (
    specifiers.some(
      (specifier) => NETWORK_MODULES.has(specifier) && !allowedSmokeModules.has(specifier),
    )
  ) {
    addViolation(
      violations,
      "smoke-network-module-forbidden",
      relative,
      "Creator smoke may use net only to reserve a loopback port; other network modules are forbidden.",
    );
  }

  if (
    /\bnet\s*\.\s*(?:connect|createConnection|Socket)\b/.test(content) ||
    /\b(?:connect|createConnection)\s*\(/.test(content) ||
    /\bnew\s+Socket\s*\(/.test(content)
  ) {
    addViolation(
      violations,
      "smoke-net-client-forbidden",
      relative,
      "Creator smoke may not create outbound net clients or sockets.",
    );
  }

  for (const name of NETWORK_GLOBAL_NAMES.filter(
    (candidate) => candidate !== FETCH_GLOBAL_NAME,
  )) {
    if (new RegExp(`\\b${name}\\b`).test(content)) {
      addViolation(
        violations,
        "smoke-network-capability-forbidden",
        relative,
        "Creator smoke may use only its fixed loopback request helper.",
      );
    }
  }

  const fetchReferences = [
    ...content.matchAll(new RegExp(`\\b${FETCH_GLOBAL_NAME}\\b`, "g")),
  ];
  const onlyFixedFetch =
    fetchReferences.length > 0 &&
    fetchReferences.every((match) =>
      new RegExp(`^${FETCH_GLOBAL_NAME}\\s*\\(\\s*url\\s*(?:,|\\))`).test(
        content.slice(match.index),
      ),
    );
  if (!onlyFixedFetch) {
    addViolation(
      violations,
      "smoke-request-target-not-fixed",
      relative,
      "Creator smoke may call only its fixed loopback URL request.",
    );
  }

  for (const match of content.matchAll(/\b(?:https?|wss?):\/\/([A-Za-z0-9.:[\]-]+)/g)) {
    const host = match[1].replace(/^\[/, "").replace(/\]$/, "").split(":", 1)[0];
    if (!LOOPBACK_HOSTS.has(host)) {
      addViolation(
        violations,
        "script-network-not-loopback",
        relative,
        "Creator smoke may use loopback endpoints only.",
      );
    }
  }
}

function checkScriptNetwork(relative, content, specifiers, violations) {
  if (relative === "scripts/smoke-creator.mjs") {
    checkSmokeNetwork(relative, content, specifiers, violations);
    return;
  }
  if (
    NETWORK_GLOBAL_NAMES.some((name) =>
      new RegExp(`\\b${name}\\b`).test(content),
    ) ||
    usesNetworkModule(specifiers) ||
    hasExternalOrProtocolRelativeUrl(content)
  ) {
    addViolation(
      violations,
      "script-network-forbidden",
      relative,
      "Only the Creator smoke script may use network capabilities.",
    );
  }
}

function isRuntimeSource(relative, extension) {
  if (!RUNTIME_SOURCE_EXTENSIONS.has(extension)) {
    return false;
  }
  return ![
    "docs/",
    "scripts/",
    "tests/",
  ].some((prefix) => relative.startsWith(prefix));
}

function scanExecutable(moduleRoot, absolute, relative, content, violations) {
  const extension = path.extname(absolute).toLowerCase();
  const specifiers = extractModuleSpecifiers(content, extension);
  for (const rawSpecifier of specifiers) {
    const specifier = rawSpecifier.split(/[?#]/, 1)[0];
    if (
      specifier.startsWith(".") ||
      isAbsoluteOnAnyPlatform(specifier) ||
      /^(?:file|link):/.test(specifier)
    ) {
      const reference = specifier.replace(/^(?:file|link):/, "");
      checkPathReference({
        moduleRoot,
        baseDirectory: path.dirname(absolute),
        reference,
        relative,
        violations,
        rule: "import-outside-module",
      });
    }
  }

  checkDynamicModuleLoads(relative, content, violations);
  checkGenericPathLiterals(moduleRoot, absolute, relative, content, violations);

  if (containsLocalAbsolutePath(content)) {
    addViolation(
      violations,
      "absolute-local-path",
      relative,
      "Executable source contains a local absolute path.",
    );
  }

  if (isRuntimeSource(relative, extension)) {
    checkRuntimeNetwork(relative, content, specifiers, violations);
  }

  const inCreator =
    relative === "apps/creator-web/index.html" ||
    relative.startsWith("apps/creator-web/src/");
  if (inCreator) {
    const creatorRules = [
      ["creator-environment", /\b(?:import\.meta\.env|process\.env)\b/],
      ["creator-storage", /\b(?:localStorage|sessionStorage|indexedDB)\b/],
    ];
    for (const [rule, pattern] of creatorRules) {
      if (pattern.test(content)) {
        addViolation(
          violations,
          rule,
          relative,
          "Creator source uses a capability forbidden by the module boundary.",
        );
      }
    }
    if (relative === "apps/creator-web/index.html") {
      checkCreatorHtmlNetwork(relative, content, violations);
    }
  }

  if (relative.startsWith("scripts/")) {
    checkScriptNetwork(relative, content, specifiers, violations);
  }
}

function isExecutableText(absolute) {
  return (
    EXECUTABLE_EXTENSIONS.has(path.extname(absolute).toLowerCase()) ||
    EXECUTABLE_NAMES.has(path.basename(absolute))
  );
}

function isTextFile(absolute) {
  const basename = path.basename(absolute);
  return (
    TEXT_EXTENSIONS.has(path.extname(absolute).toLowerCase()) ||
    TEXT_NAMES.has(basename) ||
    basename.startsWith(".env")
  );
}

function bufferLooksText(buffer) {
  if (buffer.length === 0) {
    return true;
  }
  let controlBytes = 0;
  for (const byte of buffer) {
    if (byte === 0) {
      return false;
    }
    if (byte < 9 || (byte > 13 && byte < 32)) {
      controlBytes += 1;
    }
  }
  return controlBytes / buffer.length < 0.01;
}

function looksLikePlaceholder(value) {
  const normalized = value.trim().toLowerCase();
  return (
    normalized.includes("${") ||
    normalized.includes("placeholder") ||
    normalized.includes("changeme") ||
    normalized.includes("replace_me") ||
    normalized.includes("your_") ||
    normalized.includes("example") ||
    normalized.includes("redacted") ||
    /^<[^>]+>$/.test(normalized)
  );
}

function containsHighConfidenceSecret(content) {
  if (STATIC_SECRET_PATTERNS.some((pattern) => pattern.test(content))) {
    return true;
  }
  ASSIGNED_SECRET.lastIndex = 0;
  for (const match of content.matchAll(ASSIGNED_SECRET)) {
    const value = match[1] ?? match[2] ?? match[3] ?? "";
    if (value.length >= 12 && !looksLikePlaceholder(value)) {
      return true;
    }
  }
  return false;
}

function isRotatedLogName(name) {
  return /\.log(?:[.-][A-Za-z0-9_-]+)+(?:\.gz)?$/i.test(name);
}

function matchesPathOrDescendant(candidate, expectedRoot) {
  return candidate === expectedRoot || candidate.startsWith(`${expectedRoot}/`);
}

function isAllowedAddonPath(relative, policy) {
  return policy.artifactRestrictions.allowedAddonRoots.some((allowedRoot) =>
    matchesPathOrDescendant(relative, allowedRoot)
  );
}

function isForbiddenFile(relative, policy) {
  const name = path.posix.basename(relative);
  if (policy.forbiddenTrackedFileNames.includes(name)) {
    return true;
  }
  if (name.startsWith(".env.") && name !== ".env.example") {
    return true;
  }
  if (isRotatedLogName(name)) {
    return true;
  }
  if (isAllowedAddonPath(relative, policy)) {
    return classifyForbiddenArtifact(relative, name, policy) !== null;
  }
  if (policy.forbiddenTrackedExtensions.some((extension) =>
    name.toLowerCase().endsWith(extension.toLowerCase()),
  )) {
    return true;
  }
  return classifyForbiddenArtifact(relative, name, policy) !== null;
}

function classifyForbiddenArtifact(relative, name, policy) {
  const extension = path.extname(name).toLowerCase();
  if (policy.artifactRestrictions.forbiddenBinaryExtensions.includes(extension)) {
    return "binary-artifact-forbidden";
  }
  if (isAllowedAddonPath(relative, policy)) {
    return null;
  }
  const inGodotRoot = matchesPathOrDescendant(
    relative,
    policy.artifactRestrictions.allowedGodotRoot,
  );
  if (policy.artifactRestrictions.allowedGodotFileNames.includes(name)) {
    return relative === `${policy.artifactRestrictions.allowedGodotRoot}/${name}`
      ? null
      : "godot-artifact-forbidden";
  }
  if (policy.artifactRestrictions.allowedFirstPartyGodotExtensions.includes(extension)) {
    return inGodotRoot ? null : "godot-artifact-forbidden";
  }
  if (policy.artifactRestrictions.forbiddenGodotExtensions.includes(extension)) {
    return "godot-artifact-forbidden";
  }
  if (policy.artifactRestrictions.rotatedLogsForbidden && isRotatedLogName(name)) {
    return "rotated-log-forbidden";
  }
  return null;
}

async function discoverModuleFiles(moduleRoot, policy, violations) {
  const generated = new Set(policy.generatedPaths);
  const godotRoot = policy.artifactRestrictions.allowedGodotRoot;
  const addonsRoot = `${godotRoot}/${policy.artifactRestrictions.restrictedAddonDirectoryName}`;
  const rootReal = await fs.realpath(moduleRoot);
  const files = [];

  async function visit(directory) {
    const entries = await fs.readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.name === ".git") {
        continue;
      }
      const absolute = path.join(directory, entry.name);
      const relative = relativePath(moduleRoot, absolute);
      const stats = await fs.lstat(absolute);

      if (stats.isSymbolicLink()) {
        try {
          const targetReal = await fs.realpath(absolute);
          if (!isWithin(rootReal, targetReal)) {
            addViolation(
              violations,
              "external-symlink",
              relative,
              "Symbolic link resolves outside the module boundary.",
            );
          }
        } catch {
          addViolation(
            violations,
            "broken-symlink",
            relative,
            "Symbolic link target cannot be resolved.",
          );
        }
        continue;
      }

      if (stats.isDirectory()) {
        const isRestrictedAddonDirectory =
          entry.name === policy.artifactRestrictions.restrictedAddonDirectoryName &&
          relative !== addonsRoot;
        const isUnknownAddonChild =
          relative.startsWith(`${addonsRoot}/`) &&
          !policy.artifactRestrictions.allowedAddonRoots.some((allowedRoot) =>
            matchesPathOrDescendant(relative, allowedRoot) ||
            matchesPathOrDescendant(allowedRoot, relative)
          );
        if (isRestrictedAddonDirectory || isUnknownAddonChild) {
          addViolation(
            violations,
            "godot-addon-directory-forbidden",
            relative,
            "Only the exact approved Godot addon root is allowed.",
          );
          continue;
        }
        if (!generated.has(entry.name)) {
          await visit(absolute);
        }
        continue;
      }

      if (stats.isFile()) {
        if (
          relative.startsWith(`${addonsRoot}/`) &&
          !isAllowedAddonPath(relative, policy)
        ) {
          addViolation(
            violations,
            "godot-addon-path-forbidden",
            relative,
            "Files under addons must belong to the exact approved vendor root.",
          );
        }
        files.push(absolute);
      }
    }
  }

  await visit(moduleRoot);
  return files;
}

function gitOperationalError(operation) {
  const error = new Error(`Git operation failed: ${operation}`);
  error.code = "BOUNDARY_GIT_OPERATIONAL_ERROR";
  return error;
}

export function collectGitTrackedFiles(moduleRoot) {
  const rootResult = spawnSync("git", ["rev-parse", "--show-toplevel"], {
    cwd: moduleRoot,
    encoding: "utf8",
    shell: false,
    windowsHide: true,
  });
  if (rootResult.error || rootResult.status !== 0 || !rootResult.stdout.trim()) {
    throw gitOperationalError("rev-parse");
  }

  const gitRoot = path.resolve(rootResult.stdout.trim());
  if (!isWithin(gitRoot, moduleRoot)) {
    throw gitOperationalError("module-scope");
  }
  const scope = toPosix(path.relative(gitRoot, moduleRoot)) || ".";
  const filesResult = spawnSync("git", ["ls-files", "-z", "--", scope], {
    cwd: gitRoot,
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
    shell: false,
    windowsHide: true,
  });
  if (filesResult.error || filesResult.status !== 0) {
    throw gitOperationalError("ls-files");
  }

  return filesResult.stdout
    .split("\0")
    .filter(Boolean)
    .map((tracked) => path.resolve(gitRoot, tracked))
    .filter((absolute) => isWithin(moduleRoot, absolute))
    .map((absolute) => relativePath(moduleRoot, absolute));
}

export async function auditBoundary({ moduleRoot, policy, trackedFiles }) {
  const resolvedRoot = path.resolve(moduleRoot);
  const violations = [];
  const tracked = trackedFiles ?? collectGitTrackedFiles(resolvedRoot);
  validatePolicy(policy, violations);
  if (violations.length > 0) {
    return {
      ok: false,
      checkedFiles: 0,
      trackedFiles: tracked.length,
      violations: violations.map(({ key: _key, ...violation }) => violation),
    };
  }

  const discoveredFiles = await discoverModuleFiles(resolvedRoot, policy, violations);
  const generated = new Set(policy.generatedPaths);

  for (const trackedFile of tracked) {
    const normalized = toPosix(trackedFile).replace(/^\.\//, "");
    const segments = normalized.split("/");
    if (segments.some((segment) => generated.has(segment))) {
      addViolation(
        violations,
        "tracked-generated-path",
        normalized,
        "Generated paths must not be tracked by Git.",
      );
    }
    if (isForbiddenFile(normalized, policy)) {
      addViolation(
        violations,
        "tracked-sensitive-file",
        normalized,
        "Sensitive, Godot, binary, and log files must not be tracked by Git.",
      );
    }
  }

  for (const absolute of discoveredFiles) {
    const relative = relativePath(resolvedRoot, absolute);
    const basename = path.basename(absolute);
    if (isForbiddenFile(relative, policy)) {
      addViolation(
        violations,
        "forbidden-file",
        relative,
        "Sensitive, Godot, binary, and log files are forbidden in the module tree.",
      );
    }
    const forbiddenArtifact = classifyForbiddenArtifact(relative, basename, policy);
    if (forbiddenArtifact) {
      addViolation(
        violations,
        forbiddenArtifact,
        relative,
        "This artifact type is explicitly forbidden by the active-round boundary.",
      );
    }

    if (isAllowedAddonPath(relative, policy)) {
      continue;
    }

    const bytes = await fs.readFile(absolute);
    if (!isTextFile(absolute) && !bufferLooksText(bytes)) {
      continue;
    }
    const content = bytes.toString("utf8");
    if (containsHighConfidenceSecret(content)) {
      addViolation(
        violations,
        "secret-content",
        relative,
        "Text file contains a high-confidence secret-like value.",
      );
    }
    if (!isExecutableText(absolute)) {
      continue;
    }

    if (basename === "package.json") {
      checkManifest(resolvedRoot, absolute, relative, content, policy, violations);
    } else if (basename === "package-lock.json") {
      checkLockfile(resolvedRoot, absolute, relative, content, policy, violations);
    }
    if (/^tsconfig(?:\.[^.]+)?\.json$/i.test(basename)) {
      checkTsconfig(resolvedRoot, absolute, relative, content, violations);
    }
    scanExecutable(resolvedRoot, absolute, relative, content, violations);
  }

  return {
    ok: violations.length === 0,
    checkedFiles: discoveredFiles.length,
    trackedFiles: tracked.length,
    violations: violations.map(({ key: _key, ...violation }) => violation),
  };
}
