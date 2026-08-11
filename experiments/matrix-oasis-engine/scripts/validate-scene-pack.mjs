import path from "node:path";
import { fileURLToPath } from "node:url";
import { validateSceneBundle } from "./lib/scene-pack-bundle-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
function parse(argv) { const result = {json: false}; const seen = new Set(); for (let i = 0; i < argv.length; i += 1) { const token = argv[i]; if (seen.has(token)) return null; if (token === "--json") { seen.add(token); result.json = true; } else if (["--scene", "--runtime-pack", "--runtime-receipt"].includes(token) && typeof argv[i + 1] === "string" && !argv[i + 1].startsWith("--")) { seen.add(token); result[token.slice(2)] = argv[++i]; } else return null; } return result.scene && result["runtime-pack"] && result["runtime-receipt"] ? result : null; }
const options = parse(process.argv.slice(2));
if (!options) { process.stderr.write("SCENE_PACK_CLI_ARGUMENT_ERROR\n"); process.exitCode = 2; } else {
  try { const result = await validateSceneBundle({moduleRoot, scenePath: options.scene, runtimePackPath: options["runtime-pack"], runtimeReceiptPath: options["runtime-receipt"]}); if (options.json) process.stdout.write(`${JSON.stringify(result)}\n`); else if (result.valid) process.stdout.write("SCENE_PACK_VALID\n"); else for (const item of result.diagnostics) process.stderr.write(`${item.code} ${item.path}\n`); process.exitCode = result.valid ? 0 : 1; } catch { process.stderr.write("SCENE_PACK_CLI_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
