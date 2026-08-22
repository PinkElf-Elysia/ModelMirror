import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { collectPrototypeRuntimeEvidence, createGodotRuntimeEvidenceRunner } from "../src/index.mjs";

test("runner requires a single absolute Godot binary field",()=>{assert.throws(()=>createGodotRuntimeEvidenceRunner({godotBin:"relative.exe"}),/PROTOTYPE_RUNTIME_EVIDENCE_INTERNAL_ERROR/);assert.doesNotThrow(()=>createGodotRuntimeEvidenceRunner({godotBin:path.resolve("godot.exe")}));});
test("collector rejects malformed inputs before process or filesystem work",async()=>{const runner=createGodotRuntimeEvidenceRunner({godotBin:path.resolve("godot.exe")});const result=await collectPrototypeRuntimeEvidence({replayPlanJson:"{}",previewFiles:new Map()},runner);assert.equal(result.ok,false);assert.equal(result.diagnostics[0].code,"PROTOTYPE_RUNTIME_EVIDENCE_REPLAY_PLAN_INVALID");});
