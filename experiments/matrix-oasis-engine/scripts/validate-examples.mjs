import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { validateAuthoringGamePackJson } from "@matrix-oasis/game-pack-validator";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");
const expectedExamples = Object.freeze([
  Object.freeze({
    name: "last-train-r1.authoring-game-pack.json",
    sha256: "c98b277d8e960404658f530eeb11ccee5faec2829032711ca02be3fdd827bf98",
  }),
  Object.freeze({
    name: "mechanics-conformance.authoring-game-pack.json",
    sha256: "55896eaa631f2b563df163f77002924e4e6ea1d3a9d421dc383e777c172aa119",
  }),
]);
const decoder = new TextDecoder("utf-8", { fatal: true });

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

let failed = false;

for (const example of expectedExamples) {
  let bytes;
  try {
    bytes = await readFile(path.join(examplesRoot, example.name));
  } catch {
    console.error(`EXAMPLE_READ_FAILED\t${example.name}`);
    failed = true;
    continue;
  }

  const actualSha256 = sha256(bytes);
  if (actualSha256 !== example.sha256) {
    console.error(
      `EXAMPLE_BYTES_CHANGED\t${example.name}\texpected=${example.sha256}\tactual=${actualSha256}`,
    );
    failed = true;
  }

  let text;
  try {
    text = decoder.decode(bytes);
  } catch {
    console.error(`EXAMPLE_UTF8_INVALID\t${example.name}`);
    failed = true;
    continue;
  }

  let report;
  try {
    report = validateAuthoringGamePackJson(text);
  } catch {
    console.error(`EXAMPLE_VALIDATOR_FAILED\t${example.name}`);
    failed = true;
    continue;
  }

  if (!report.valid) {
    for (const diagnostic of report.diagnostics) {
      console.error(
        `EXAMPLE_INVALID\t${example.name}\t${diagnostic.phase}\t${diagnostic.code}\t${diagnostic.path || "/"}`,
      );
    }
    failed = true;
    continue;
  }

  if (actualSha256 === example.sha256) {
    console.log(
      `EXAMPLE_VALID\t${example.name}\tsha256=${actualSha256}`,
    );
  }
}

if (failed) {
  process.exitCode = 1;
} else {
  console.log(`EXAMPLES_VALID\tcount=${expectedExamples.length}`);
}
