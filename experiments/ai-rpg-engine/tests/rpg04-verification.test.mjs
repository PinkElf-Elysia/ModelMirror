import test from "node:test";
import assert from "node:assert/strict";
import { checkBudget, checkFileRecords, sameDependencies } from "../scripts/verify-rpg04.mjs";
test("aggregate rejects exhausted budget rewrites and duplicate dispatches", () => {
 const budget = { authorizedDispatches: 10, consumed: 10, remaining: 0, automaticRetry: false, entries: Array.from({length:10}, (_,i)=>({id:String(i)})) };
 assert.doesNotThrow(()=>checkBudget(budget));
 for (const patch of [{remaining:1},{consumed:9},{authorizedDispatches:11},{automaticRetry:true},{entries:Array(10).fill({id:"same"})}]) assert.throws(()=>checkBudget({...budget,...patch}));
});
test("aggregate rejects missing, duplicate and escaping hash inventories", () => {
 for (const records of [[],[{path:"../escape"}],[{path:"same"},{path:"same"}]]) assert.throws(()=>checkFileRecords(".",records));
});

test("dependency comparison ignores key order but rejects changed versions or packages", () => {
 assert.equal(sameDependencies({a:"1",b:"2"},{b:"2",a:"1"}),true);
 assert.equal(sameDependencies({a:"1"},{a:"2"}),false);
 assert.equal(sameDependencies({a:"1"},{a:"1",b:"2"}),false);
});
