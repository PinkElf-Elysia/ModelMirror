import { canonicalizeJsonValue } from "@matrix-oasis/scene-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";
import { diagnostic, report } from "./diagnostics.mjs";
import { parseSceneJson } from "./json-document.mjs";
import { validateSceneSemantics } from "./semantic-validator.mjs";
import { validateSceneStructure } from "./structural-validator.mjs";

export class ScenePackValidatorOperationalError extends Error { constructor() { super("SCENE_PACK_VALIDATOR_INTERNAL_ERROR"); this.name = "ScenePackValidatorOperationalError"; this.code = "SCENE_PACK_VALIDATOR_INTERNAL_ERROR"; } }

export async function validateScenePackJson(sceneText, runtimeText, receiptText) {
  try {
    const runtimeReport = await validateRuntimeGamePackJson(runtimeText, receiptText); if (!runtimeReport.valid) return runtimeReport;
    const parsed = parseSceneJson(sceneText); if (parsed.diagnostics.length) return report(parsed.diagnostics);
    const schema = validateSceneStructure(parsed.value); if (schema.length) return report(schema);
    const runtimePack = JSON.parse(runtimeText); const receipt = JSON.parse(receiptText);
    const semantic = validateSceneSemantics(parsed.value, runtimePack, receipt); if (semantic.length) return report(semantic);
    if (canonicalizeJsonValue(parsed.value) !== sceneText) return report([diagnostic("integrity", "SCENE_PACK_JSON_NON_CANONICAL", "/scenePack")]);
    return report([]);
  } catch { throw new ScenePackValidatorOperationalError(); }
}
