#!/usr/bin/env node
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  R12_QUALIFICATION_MARKER,
  parseR12CallArguments,
  verifyR12CreatorPublishedQualification,
} from "./lib/r12-qualification-core.mjs";

export function parseR12QualificationArguments(args) {
  return parseR12CallArguments(args);
}

export async function runR12QualificationVerification({ args, dependencies = {} }) {
  const parsed = parseR12QualificationArguments(args);
  const verify = dependencies.verify ?? verifyR12CreatorPublishedQualification;
  return verify(parsed, dependencies.verifierDependencies ?? {});
}

async function main() {
  try {
    const result = await runR12QualificationVerification({ args: process.argv.slice(2) });
    if (!result.ok) {
      const lines = result.diagnostics.map((diagnostic) =>
        `${typeof diagnostic?.code === "string" ? diagnostic.code : "R12_QUALIFICATION_REJECTED"} ${
          typeof diagnostic?.path === "string" ? diagnostic.path : ""}`.trim());
      process.stderr.write(`${lines.join("\n")}\n`);
      process.exitCode = 1;
      return;
    }
    process.stdout.write(`${R12_QUALIFICATION_MARKER}${JSON.stringify(result.evidence)}\n`);
  } catch {
    process.stderr.write("R12_QUALIFICATION_INTERNAL_ERROR\n");
    process.exitCode = 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  await main();
}
