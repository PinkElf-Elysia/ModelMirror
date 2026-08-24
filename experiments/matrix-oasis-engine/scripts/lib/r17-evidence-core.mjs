import fs from "node:fs";
import path from "node:path";
import { verifyQualificationDirectory } from "@matrix-oasis/v2-qualification-harness";

function fail(code) { const error = new Error(code); error.code = code; throw error; }

export function verifyR17EvidenceRoot(root) {
  const names = fs.readdirSync(root).sort();
  const direct = names.includes("qualification-report.json");
  const directories = direct ? [root] : names.map((name) => path.join(root, name));
  if (directories.length === 0) fail("R17_EVIDENCE_EMPTY");
  const reports = directories.map((directory) => verifyQualificationDirectory(directory));
  const ids = reports.map((report) => report.candidateId);
  if (new Set(ids).size !== ids.length) fail("R17_EVIDENCE_DUPLICATE_CANDIDATE");
  return Object.freeze({ ok: true, candidates: Object.freeze(ids.sort()), reports: reports.length });
}
