#!/usr/bin/env node
import {
  R12_QUALIFICATION_MARKER,
  parseR12CacheVerificationArguments,
  verifyR12NeutralSpatialCache,
} from "./lib/r12-qualification-core.mjs";

try {
  const options = parseR12CacheVerificationArguments(process.argv.slice(2));
  const result = await verifyR12NeutralSpatialCache(options);
  if (!result.ok) {
    console.error(JSON.stringify(result));
    process.exit(1);
  }
  console.log(`${R12_QUALIFICATION_MARKER}${JSON.stringify(result.evidence)}`);
} catch {
  console.error("R12_QUALIFICATION_INTERNAL_ERROR");
  process.exit(2);
}
