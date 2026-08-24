import { verifyR17EvidenceRoot } from "./lib/r17-evidence-core.mjs";

try {
  const argv = process.argv.slice(2);
  if (argv.length !== 2 || argv[0] !== "--evidence-root") throw Object.assign(new Error("R17_EVIDENCE_ARGUMENT_ERROR"), { code: "R17_EVIDENCE_ARGUMENT_ERROR" });
  const result = verifyR17EvidenceRoot(argv[1]);
  console.log(`R17_EVIDENCE_OK reports=${result.reports} candidates=${result.candidates.join(",")}`);
} catch (error) { console.error(error?.code ?? "R17_EVIDENCE_INTERNAL_ERROR"); process.exitCode = 1; }
