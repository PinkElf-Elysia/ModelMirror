import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { assertR22ReferenceDirectory, assertR22ReferenceLock } from "../scripts/verify-r22-references.mjs";

const root=path.resolve(fileURLToPath(new URL("..",import.meta.url)));
const lock=JSON.parse(readFileSync(path.join(root,"third-party","npc-cognition-references","reference.lock.json"),"utf8"));
const clone=()=>structuredClone(lock);

test("R22 references lock direct-license-only evidence and zero production dependencies",()=>{
  assert.equal(assertR22ReferenceLock(clone()),true);
  assert.equal(lock.implementationDecision,"internal-bounded-cognition-native-control");
  assert.equal(lock.references.every(({license,reuse})=>license.closure.endsWith("transitive-unverified")&&reuse!=="production-dependency"),true);
});
test("R22 reference validation rejects commit, tag target, tree, license, and conclusion drift",()=>{
  for(const mutate of [
    (value)=>{value.references[0].commit="0".repeat(40)},
    (value)=>{value.references[1].tagObjectSha1="0".repeat(40)},
    (value)=>{value.references[2].gitTreeSha1="0".repeat(40)},
    (value)=>{value.references[3].license.sha256="0".repeat(64)},
    (value)=>{value.references[4].conclusionCode="UNREVIEWED"},
  ]){const value=clone();mutate(value);assert.throws(()=>assertR22ReferenceLock(value));}
});
test("R22 reference validation rejects order, duplicate ids, production reuse, and overstated closure",()=>{
  const reordered=clone();reordered.references.reverse();assert.throws(()=>assertR22ReferenceLock(reordered));
  const duplicate=clone();duplicate.references[1].id=duplicate.references[0].id;assert.throws(()=>assertR22ReferenceLock(duplicate));
  const production=clone();production.references[0].reuse="production-dependency";assert.throws(()=>assertR22ReferenceLock(production));
  const closure=clone();closure.references[0].license.closure="qualified";assert.throws(()=>assertR22ReferenceLock(closure));
});
test("R22 reference validation rejects candidate dependencies and committed candidate artifacts",()=>{
  for(const section of ["dependencies","devDependencies","optionalDependencies","peerDependencies"]) assert.throws(()=>assertR22ReferenceLock(clone(),[JSON.stringify({[section]:{"@langchain/langgraph":"1.0.5"}})]));
  assert.equal(assertR22ReferenceDirectory(["reference.lock.json"]),true);
  assert.throws(()=>assertR22ReferenceDirectory(["reference.lock.json","candidate.gd"]));
  assert.throws(()=>assertR22ReferenceDirectory(["reference.lock.json","plugin.dll"]));
});
