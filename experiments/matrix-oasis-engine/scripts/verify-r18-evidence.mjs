import { verifyR18EvidenceRoot } from "./lib/r18-harness-core.mjs";

try {
  const argv = process.argv.slice(2);
  if (argv.length !== 2 || argv[0] !== "--evidence-root") throw Object.assign(new Error("argument"), { code: "R18_EVIDENCE_ARGUMENT_INVALID" });
  const report = verifyR18EvidenceRoot(argv[1]);
  process.stdout.write(`R18_EVIDENCE_OK reports=${report.reports} candidates=${report.candidates.join(",")}\n`);
} catch (error) {
  process.stderr.write(`${error?.code || "R18_EVIDENCE_INTERNAL_ERROR"}\n`);
  process.exitCode = 2;
}
