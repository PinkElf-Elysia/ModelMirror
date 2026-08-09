import { validateRuntimeGamePackJson } from "../src/index.mjs";

const mode = process.argv[2];
const sentinel = `dynamic-private-sentinel-${mode}`;
const deepArray = `${"[".repeat(5_001)}0${"]".repeat(5_001)}`;

let runtimeText = "{}";
let receiptText = "{}";

if (mode === "runtime-generic") {
  runtimeText = `{${JSON.stringify(sentinel)}:${deepArray}}`;
} else if (mode === "receipt-generic") {
  receiptText = `{${JSON.stringify(sentinel)}:${deepArray}}`;
} else if (mode === "runtime-condition") {
  const condition =
    `${'{"op":"not","condition":'.repeat(2_001)}` +
    `{"op":"eq","variableIndex":0,"value":${JSON.stringify(sentinel)}}` +
    "}".repeat(2_001);
  runtimeText = `{"nodes":[{"actions":[{"when":${condition}}]}]}`;
} else {
  process.exitCode = 2;
  throw new Error("UNKNOWN_DEEP_INPUT_MODE");
}

const report = await validateRuntimeGamePackJson(runtimeText, receiptText);
process.stdout.write(JSON.stringify(report));
