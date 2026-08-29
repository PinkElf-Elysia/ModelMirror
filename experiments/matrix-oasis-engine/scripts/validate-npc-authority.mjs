import fs from "node:fs/promises";
import {
  validateDerivedProjectionManifestJson,
  validateNpcAdjudicationResultJson,
  validateNpcAuthorityPolicyJson,
  validateNpcIntentJson,
  validateWorldEventLedgerJson,
  validateWorldEventLedgerReplayReportJson,
} from "@matrix-oasis/npc-authority-contracts";
import { executeValidateNpcAuthorityCli } from "./lib/r19-cli-core.mjs";

const result = await executeValidateNpcAuthorityCli({
  args: process.argv.slice(2),
  services: { lstat: fs.lstat, realpath: fs.realpath, openFile: fs.open },
  validators: {
    policy: validateNpcAuthorityPolicyJson,
    intent: validateNpcIntentJson,
    result: validateNpcAdjudicationResultJson,
    ledger: validateWorldEventLedgerJson,
    projection: validateDerivedProjectionManifestJson,
    replay: validateWorldEventLedgerReplayReportJson,
  },
});
process.stdout.write(result.stdout);
process.stderr.write(result.stderr);
process.exitCode = result.exitCode;
