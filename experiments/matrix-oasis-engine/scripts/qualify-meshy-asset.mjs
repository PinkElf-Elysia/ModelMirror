import { lstat, mkdir, open, realpath } from "node:fs/promises";
import path from "node:path";
import {
  MESHY_PROVIDER_ENDPOINT,
  createMeshyTextTo3DProvider,
  planPrototypeAssets,
} from "@matrix-oasis/prototype-asset-pipeline";
import { executeQualifyMeshyAssetCli } from "./lib/prototype-asset-cli-core.mjs";

const apiKey = process.env.MATRIX_OASIS_MESHY_API_KEY;
let result;
if (typeof apiKey !== "string" || apiKey.length < 1 || apiKey.length > 8192 || /[\r\n]/u.test(apiKey)) {
  result = { exitCode: 2, stdout: "", stderr: "MESHY_QUALIFICATION_CONFIG_INVALID\n" };
} else {
  const tempRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
  result = await executeQualifyMeshyAssetCli({
    args: process.argv.slice(2),
    tempRoot,
    qualificationRoot: path.join(tempRoot, "matrix-oasis-r9-qualification-meshy-20260811"),
    services: { lstat, mkdir, openFile: open, realpath },
    provider: createMeshyTextTo3DProvider({ endpoint: MESHY_PROVIDER_ENDPOINT, apiKey }),
    planPrototypeAssets,
  });
}
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exitCode = result.exitCode;
