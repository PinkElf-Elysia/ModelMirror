import fs from "node:fs";
import path from "node:path";
import {createHash} from "node:crypto";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const modulePrefix = "experiments/ai-rpg-engine/";
const base = "81fc14f6e0dada2447a63c1e532ee55b1f914ded";
const branch = "codex/ai-rpg-rpg05-ui";
const failures = [];
function git(args) {
  const r = spawnSync("git", args, {cwd:repo, encoding:"utf8", windowsHide:true, maxBuffer:32*1024*1024});
  if(r.status !== 0) throw new Error("RPG05_GIT_FAILED");
  return r.stdout;
}
const sha = b => createHash("sha256").update(b).digest("hex");
const json = p => JSON.parse(fs.readFileSync(path.join(repo,p),"utf8").replace(/^\uFEFF/,""));
const baseline = json(modulePrefix+"docs/RPG05_BASELINE.json");
if (git(["rev-parse","HEAD"]).trim() !== base) failures.push("HEAD_NOT_FIXED_BASE");
if (git(["branch","--show-current"]).trim() !== branch) failures.push("WRONG_BRANCH");
if (baseline.base !== base) failures.push("BASELINE_ID");
const listed=git(["ls-tree","-r",base,"--",modulePrefix,"docs/ai-rpg-experiment/"]).trim().split("\n").filter(Boolean).map(line=>{
  const [meta,p]=line.split("\t"); return {path:p,blob:meta.split(" ")[2]};
});
if (listed.length !== baseline.files.length) failures.push("FREEZE_SET_COUNT");
const mutable = new Set(baseline.mutable);
const indexEntries = new Map(git(["ls-files", "-s", "-z"]).split("\0").filter(Boolean).map(line => {
  const [meta, name] = line.split("\t"); return [name, meta.split(" ")[1]];
}));
for (const entry of listed) {
  const frozen=baseline.files.find(x=>x.path===entry.path);
  if (!frozen || frozen.blob !== entry.blob) { failures.push("BASELINE_RECORD:"+entry.path); continue; }
  if (mutable.has(entry.path)) continue;
  const file=path.join(repo,entry.path);
  if (!fs.existsSync(file) || fs.lstatSync(file).isSymbolicLink() || sha(fs.readFileSync(file))!==frozen.checkoutSha256) failures.push("FROZEN_BYTES:"+entry.path);
  const index=indexEntries.get(entry.path);
  if(index!==entry.blob) failures.push("FROZEN_INDEX:"+entry.path);
}
const changed = new Set([...git(["diff","--name-only","--no-renames",base,"-z"]).split("\0"),...git(["ls-files","--others","--exclude-standard","-z"]).split("\0")].filter(Boolean));
const approvedServerPaths = new Set(["server/main.py", "server/tests/test_provider_chat_structured_output.py"]);
for(const p of changed){
  if(!p.startsWith(modulePrefix)&&!p.startsWith("docs/ai-rpg-experiment/")&&!approvedServerPaths.has(p)) failures.push("OUTSIDE_SCOPE:"+p);
  if(p.split("/").some(s=>["node_modules","dist","coverage",".rpg05-work",".env"].includes(s))) failures.push("GENERATED_OR_SECRET:"+p);
}
const approvals=json(modulePrefix+"docs/RPG05_PROTOTYPES.json");
for(const record of approvals.records){
  if(!["approved","approved_with_amendments"].includes(record.status)||!record.sha256) failures.push("PROTOTYPE_NOT_BOUND:"+record.id);
  if(!fs.existsSync(record.sourcePath)||sha(fs.readFileSync(record.sourcePath))!==record.sha256) failures.push("PROTOTYPE_SOURCE:"+record.id);
}
console.log(JSON.stringify({gate:"RPG05_INITIAL_BOUNDARY",status:failures.length?"failed":"passed",base,branch,frozenFiles:baseline.files.length,prototypeHashes:approvals.records.length,changedFiles:changed.size,scope:"initial freeze and approval source integrity only; not build/runtime/security acceptance",failures},null,2));
process.exitCode=failures.length?1:0;
