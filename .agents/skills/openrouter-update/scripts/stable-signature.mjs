import crypto from "node:crypto";
import fs from "node:fs";

const [inputPath] = process.argv.slice(2);
if (!inputPath) {
  process.stderr.write("Usage: stable-signature.mjs <actionable-json>\n");
  process.exit(2);
}

function canonicalize(value) {
  if (Array.isArray(value)) {
    return value
      .map(canonicalize)
      .sort((left, right) =>
        JSON.stringify(left).localeCompare(JSON.stringify(right)),
      );
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort((left, right) => left.localeCompare(right))
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

const payload = JSON.parse(fs.readFileSync(inputPath, "utf8"));
const canonicalJson = JSON.stringify(canonicalize(payload));
process.stdout.write(
  crypto.createHash("sha256").update(canonicalJson, "utf8").digest("hex"),
);
