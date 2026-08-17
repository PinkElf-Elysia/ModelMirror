#!/usr/bin/env node

import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySpatialReferences } from "./lib/spatial-reference-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

try {
  const result = verifySpatialReferences(moduleRoot);
  process.stdout.write(`SPATIAL_REFERENCES_OK references=${result.references} files=${result.files} checkedPayloads=${result.checkedPayloads}\n`);
} catch (error) {
  const code = typeof error?.code === "string" ? error.code : "SPATIAL_REFERENCE_INTERNAL_ERROR";
  process.stderr.write(`${code}\n`);
  process.exitCode = 1;
}
