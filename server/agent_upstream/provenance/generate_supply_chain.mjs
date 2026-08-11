import { createHash } from "node:crypto";
import { readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";

const [deployArgument, auditArgument, outputArgument] = process.argv.slice(2);
if (!deployArgument || !auditArgument || !outputArgument) {
  throw new Error("usage: node generate_supply_chain.mjs <deploy-root> <pnpm-audit.json> <output-dir>");
}

const deployRoot = resolve(deployArgument);
const auditPath = resolve(auditArgument);
const outputRoot = resolve(outputArgument);
const blockedLicense = /(?:^|[^A-Z])(?:AGPL|GPL|SSPL)(?:-|$|[^A-Z])/i;
const unknownLicense = /^(?:UNKNOWN|UNLICENSED|NONE|NOASSERTION)$/i;

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function packageLicense(pkg) {
  const declared = typeof pkg.license === "string"
    ? pkg.license.trim()
    : Array.isArray(pkg.licenses)
      ? pkg.licenses.map((item) => typeof item === "string" ? item : item?.type).filter(Boolean).join(" OR ")
      : "";
  return declared || "UNKNOWN";
}

function packageJsonFiles(root) {
  const files = [];
  const seenDirectories = new Set();
  const visit = (directory) => {
    let real;
    try {
      real = realpathSync(directory);
    } catch {
      return;
    }
    if (seenDirectories.has(real)) return;
    seenDirectories.add(real);
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.name === "package.json" && entry.isFile()) {
        files.push(path);
      } else if (entry.isDirectory() || entry.isSymbolicLink()) {
        visit(path);
      }
    }
  };
  visit(root);
  return files;
}

function purl(name, version) {
  const encoded = name.startsWith("@")
    ? name.slice(1).split("/").map(encodeURIComponent).join("/")
    : encodeURIComponent(name);
  return `pkg:npm/${encoded}@${encodeURIComponent(version)}`;
}

const rootPackagePath = join(deployRoot, "package.json");
const rootPackageRaw = readFileSync(rootPackagePath);
const rootPackage = JSON.parse(rootPackageRaw.toString("utf8"));
const rootRef = purl(rootPackage.name, rootPackage.version);
const rootComponent = {
  type: "application",
  "bom-ref": rootRef,
  name: rootPackage.name,
  version: rootPackage.version,
  purl: rootRef,
  hashes: [{ alg: "SHA-256", content: sha256(rootPackageRaw) }],
  properties: [
    { name: "modelmirror:first-party", value: "true" },
    { name: "modelmirror:deploy-path", value: "package.json" },
  ],
};

const componentsByRef = new Map();
for (const file of packageJsonFiles(deployRoot)) {
  const raw = readFileSync(file);
  const pkg = JSON.parse(raw.toString("utf8"));
  if (!pkg.name || !pkg.version) continue;
  const bomRef = purl(pkg.name, pkg.version);
  // The deploy root is the first-party ModelMirror worker application. It is
  // represented by metadata.component, not reclassified as a third-party
  // library with an invented license declaration.
  if (bomRef === rootRef && realpathSync(file) === realpathSync(rootPackagePath)) continue;
  if (componentsByRef.has(bomRef)) continue;
  const license = packageLicense(pkg);
  componentsByRef.set(bomRef, {
    type: "library",
    "bom-ref": bomRef,
    name: pkg.name,
    version: pkg.version,
    purl: bomRef,
    hashes: [{ alg: "SHA-256", content: sha256(raw) }],
    licenses: [{ expression: license }],
    properties: [
      { name: "modelmirror:deploy-path", value: relative(deployRoot, file).replaceAll("\\", "/") },
    ],
  });
}

const components = [...componentsByRef.values()].sort((left, right) => left.purl.localeCompare(right.purl));
const licenses = components.map((component) => ({
  name: component.name,
  version: component.version,
  purl: component.purl,
  license: component.licenses[0].expression,
}));
const blocked = licenses.filter((item) => blockedLicense.test(item.license) || unknownLicense.test(item.license));

const audit = JSON.parse(readFileSync(auditPath, "utf8"));
const vulnerabilities = audit.metadata?.vulnerabilities ?? {};
const high = Number(vulnerabilities.high ?? 0);
const critical = Number(vulnerabilities.critical ?? 0);

const closureDigest = sha256(canonical(licenses));
const uuid = `${closureDigest.slice(0, 8)}-${closureDigest.slice(8, 12)}-${closureDigest.slice(12, 16)}-${closureDigest.slice(16, 20)}-${closureDigest.slice(20, 32)}`;
const sbom = {
  bomFormat: "CycloneDX",
  specVersion: "1.6",
  serialNumber: `urn:uuid:${uuid}`,
  version: 1,
  metadata: {
    component: rootComponent,
    properties: [
      { name: "modelmirror:source", value: "PenguinHarness production deploy closure" },
      { name: "modelmirror:upstream-revision", value: "047505dccc0cc16ad92be11011347d635f33ceb0" },
      { name: "modelmirror:package-manager", value: "pnpm@11.18.0" },
      { name: "modelmirror:closure-sha256", value: closureDigest },
    ],
  },
  components,
  dependencies: [{ ref: rootRef, dependsOn: components.map((item) => item.purl) }],
};
const report = {
  schema_version: 1,
  upstream_revision: "047505dccc0cc16ad92be11011347d635f33ceb0",
  package_manager: "pnpm@11.18.0",
  application_component: {
    name: rootComponent.name,
    version: rootComponent.version,
    purl: rootComponent.purl,
  },
  production_component_count: components.length,
  production_closure_sha256: closureDigest,
  license_gate: {
    status: blocked.length === 0 ? "passed" : "failed",
    blocked,
    policy: "Reject UNKNOWN, UNLICENSED, NOASSERTION, GPL, AGPL, and SSPL declarations.",
  },
  audit_gate: {
    status: high === 0 && critical === 0 ? "passed" : "failed",
    high,
    critical,
    vulnerabilities,
    policy: "Reject high or critical advisories in the production closure.",
  },
  sbom_sha256: sha256(canonical(sbom)),
};

writeFileSync(join(outputRoot, "production.cdx.json"), `${JSON.stringify(sbom, null, 2)}\n`, "utf8");
writeFileSync(join(outputRoot, "production-licenses.json"), `${JSON.stringify({ schema_version: 1, components: licenses }, null, 2)}\n`, "utf8");
writeFileSync(join(outputRoot, "production-audit.json"), `${JSON.stringify(audit, null, 2)}\n`, "utf8");
writeFileSync(join(outputRoot, "supply-chain-report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");

if (blocked.length > 0 || high > 0 || critical > 0) {
  process.exitCode = 2;
}
