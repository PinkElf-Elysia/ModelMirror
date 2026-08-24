import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";
import { assertSafeTmpPath, V2QualificationOperationalError } from "./source.mjs";

function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function fail(code) { throw new V2QualificationOperationalError(code); }

function safeParent(outputDir) {
  const target = assertSafeTmpPath(outputDir, { mustExist: false });
  if (fs.existsSync(target)) fail("R17_OUTPUT_EXISTS");
  const parent = assertSafeTmpPath(path.dirname(target), { allowRoot: true });
  if (!fs.lstatSync(parent).isDirectory()) fail("R17_OUTPUT_PARENT_INVALID");
  return { target, parent };
}

export function publishQualification({ outputDir, sourceIdentityJson, executionEvidence, report }) {
  const { target, parent } = safeParent(outputDir);
  const reportJson = canonicalizeJsonValue(report);
  if (!validateV2QualificationReportJson(reportJson).valid) fail("R17_REPORT_INVALID");
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const expectedFiles = [
    ["source-identity.json", sourceIdentityJson],
    ["execution-evidence.json", executionJson],
    ["qualification-report.json", reportJson],
  ];
  const staging = path.join(parent, `.${path.basename(target)}.staging-${crypto.randomBytes(8).toString("hex")}`);
  fs.mkdirSync(staging);
  try {
    for (const [name, contents] of expectedFiles) fs.writeFileSync(path.join(staging, name), contents, { encoding: "utf8", flag: "wx" });
    fs.renameSync(staging, target);
  } catch (error) {
    if (fs.existsSync(staging)) fs.rmSync(staging, { recursive: true, force: true });
    if (error instanceof V2QualificationOperationalError) throw error;
    fail("R17_OUTPUT_PUBLISH_FAILED");
  }
  return Object.freeze({ outputId: sha256(Buffer.from(reportJson, "utf8")), files: Object.freeze(expectedFiles.map(([name, contents]) => Object.freeze({ name, byteLength: Buffer.byteLength(contents), sha256: sha256(Buffer.from(contents, "utf8")) }))) });
}

export function verifyQualificationDirectory(directory) {
  const root = assertSafeTmpPath(directory);
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink()) fail("R17_EVIDENCE_ROOT_INVALID");
  const names = fs.readdirSync(root).sort();
  const expected = ["execution-evidence.json", "qualification-report.json", "source-identity.json"];
  if (JSON.stringify(names) !== JSON.stringify(expected)) fail("R17_EVIDENCE_FILE_SET_INVALID");
  const reportText = fs.readFileSync(path.join(root, "qualification-report.json"), "utf8");
  const validation = validateV2QualificationReportJson(reportText);
  if (!validation.valid) fail("R17_REPORT_INVALID");
  for (const file of validation.value.evidence.files) {
    const filePath = path.join(root, file.name);
    const payload = fs.readFileSync(filePath);
    if (payload.length !== file.byteLength || sha256(payload) !== file.sha256) fail("R17_EVIDENCE_HASH_MISMATCH");
  }
  return Object.freeze({ ok: true, candidateId: validation.value.candidate.id, reportSha256: sha256(Buffer.from(reportText, "utf8")) });
}
