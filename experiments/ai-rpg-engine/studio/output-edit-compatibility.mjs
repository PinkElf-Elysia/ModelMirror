import {readFile} from 'node:fs/promises';
import {sha,canonical} from '../plugins/catalog.mjs';
import {earthRuntime} from './runtime-binding.mjs';
import {modelRuntime} from './model-runtime.mjs';
import {historyRuntime} from './history-runtime.mjs';
import {summaryRuntime} from './summary-runtime.mjs';
import {memoryRuntime} from './memory-runtime.mjs';
// Exact pre-edit digests from main d9f20fe6. Translate only these reviewed
// host changes, then run the original complete runtime validator. Saved
// descriptors, request hashes and receipts are never rewritten.
const before={
  "studio/branch-archive.mjs": "f6afcf958ba27c795fe069a7a1ce8da828a3d8b231a76de46b3a3a2964081a5b",
  "card-replica/lib/store.mjs": "a14fd94f2e86f091d1088a256817c7724f3a4965d9effdaa3963ac12aa624bec",
  "studio/runtime-binding.mjs": "e603069d121e0d0b1e99daeeb671fbb04e65df5a85c642edb34adeb9e2ee57b0",
  "studio/model-runtime.mjs": "f9c48f349527caa42952fe452996214fa333db09ab90e2b2ff01e90a043338ba",
  "studio/model-session-store.mjs": "971ab21ae87206e622bca075b3fa358b5c173e458a564ac2ca7aa60d7877b56e",
  "studio/server.mjs": "5e78f75b108ec233be8b83844fc82b3739e0b6bf7fd27ba6ecfcee4fff2e1793",
  "plugins/catalog.mjs": "986b1e55645f347506a8f39bf6c86af152cf9fecde225a1b4ff6be7bcb5452ad",
  "plugins/host.mjs": "f8c541d8016d3cf5f46b6a9823a4c763edb27b9fc9113d45591458f65ca7f469",
  "studio/history-assembly.mjs": "fe64f6b8b29adf075726c65d9f7194b13d7dcf06848999500f4734e8772621e1",
  "studio/summary-assembly.mjs": "898ab39801e6272cc854305ce349d81531285c71341c00f3e7e1b9482a8e19e4",
  "studio/summary-state.mjs": "3198f0eed48f774f0ae349d2f29013818983f249b7accada2a469b0444cef8f4",
  "studio/memory-assembly.mjs": "e32cb564f08efbd2c2ee2ad03c2c677dd7d45a12942c17a15c5d7f1065599cf1",
  "studio/memory-task-state.mjs": "c08c01dbd27f4910254b4ef09365bfc8375c9e3f42f41952825a6752e2c7323f",
  "god-card/lib/adapter.mjs": "e5ef0339be873c0f3fd4e1d33ce20d6d1bd2fbc8ec2be0d91764c59a8dac1aa1",
  "god-card/lib/http.mjs": "8733bb9586635165d8e29314ea6dd1702a32899deb90fd5af5491477c671a308",
  "studio/card-session-store.mjs": "0e97151e1bae1b66c088a104ca89d73616e0accd818936ff4f1356a1dc00818d"
};
const oldBases=["05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca", "e31d4ff9cd656eb8abf8cb766ecc223b9e7b3548c0b7f179cabe0f2aebae0bbf", "f2a62c8c08b197bae80eb3cf0b76b7b7d013067acd092b417754b961f4e3ae18", "8b9ea914d2c847521c6a69220b3b5cc5bbb595b94ee803e402789269a0e86fed", "923d2b170eb3246da01ca9508000e0858ecd6ee9107de05683fc96eeaff37875"];
const newBases=[earthRuntime,modelRuntime,historyRuntime,summaryRuntime,memoryRuntime].map(r=>r.hash);
const current=Object.fromEntries(await Promise.all([...Object.keys(before),'studio/output-edits.mjs'].map(async p=>[p,sha(await readFile(new URL('../'+p,import.meta.url)))])));
export function preserveOutputEditRuntime(store){
 const original=store.status.bind(store);
 store.status=s=>{
  const result=original(s);
  if(result.code!=='RUNTIME_VERSION_MISMATCH')return result;
  const descriptor=structuredClone(s.runtime.descriptor);
  let changed=false;
  for(const key of ['artifacts','hostArtifacts']){
   const artifacts=descriptor[key];if(!artifacts)continue;
   for(const [path,old] of Object.entries(before)){
    if(artifacts[path]===old){artifacts[path]=current[path];changed=true;}
   }
   if(!Object.hasOwn(artifacts,'studio/output-edits.mjs'))artifacts['studio/output-edits.mjs']=current['studio/output-edits.mjs'];
  }
  const index=oldBases.indexOf(descriptor.baseRuntimeHash);
  if(index>=0)descriptor.baseRuntimeHash=newBases[index];
  if(!changed)return result;
  const probe=original({...s,runtime:{...s.runtime,descriptor,hash:sha(canonical(descriptor))}});
  return probe.compatible?{compatible:true,code:null,hash:s.runtime.hash}:result;
 };
}
