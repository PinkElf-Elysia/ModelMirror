import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { checkRealEvidence } from "../scripts/verify-rpg03.mjs";
const read = (name) => JSON.parse(fs.readFileSync(new URL("../docs/" + name, import.meta.url), "utf8"));
const sample = () => ({ real: read("RPG03_REAL_ACCEPTANCE.json"), ledger: read("RPG03_CALL_LEDGER.json") });
test("retained real evidence is consistent and checking does not mutate inputs", () => {
  const {real,ledger}=sample(), before=JSON.stringify({real,ledger});
  assert.equal(checkRealEvidence(real,ledger,real.moduleSourceSha256).valid,true);
  assert.equal(JSON.stringify({real,ledger}),before);
});
test("mock classification and stale source cannot qualify real acceptance", () => {
  for(const mutate of [r=>{r.evidenceKind="mock";},r=>{r.certification.response.status="failed";},r=>{r.moduleSourceSha256="0".repeat(64);}]) {
    const {real,ledger}=sample(), binding=real.moduleSourceSha256;mutate(real);
    assert.equal(checkRealEvidence(real,ledger,binding).valid,false);
  }
});
test("over-budget, missing and duplicate dispatches fail", () => {
  for(const mutate of [l=>{l.consumed=6;},l=>{l.entries.pop();},l=>{l.entries[1].id=l.entries[0].id;},l=>{l.entries[0].dispatchState="reserved";}]) {
    const {real,ledger}=sample();mutate(ledger);
    assert.equal(checkRealEvidence(real,ledger,real.moduleSourceSha256).valid,false);
  }
});
test("uncommitted continuity, missing receipt and false cancellation claims fail", () => {
  for(const mutate of [r=>{r.phases[1].record.formalTurns=1;},r=>{r.phases[0].record.receipt.serverReceipt=null;},r=>{r.phases[2].record.receipt.cancellation.clientAborted=false;},r=>{r.cleanup.ownedProcessesStopped=false;}]) {
    const {real,ledger}=sample();mutate(real);
    assert.equal(checkRealEvidence(real,ledger,real.moduleSourceSha256).valid,false);
  }
});
