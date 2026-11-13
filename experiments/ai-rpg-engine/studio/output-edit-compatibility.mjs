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
  "card-replica/lib/store.mjs": "a14fd94f2e86f091d1088a256817c7724f3a4965d9effdaa3963ac12aa624bec",
  "card-replica/lib/assembly.mjs": "7507921b7c15695d62a71e65acc4f7bf5f8d8083a9812b855f17bd5585905bed",
  "studio/branch-archive.mjs": "f6afcf958ba27c795fe069a7a1ce8da828a3d8b231a76de46b3a3a2964081a5b",
  "studio/runtime-binding.mjs": "e603069d121e0d0b1e99daeeb671fbb04e65df5a85c642edb34adeb9e2ee57b0",
  "studio/model-runtime.mjs": "f9c48f349527caa42952fe452996214fa333db09ab90e2b2ff01e90a043338ba",
  "studio/model-session-store.mjs": "971ab21ae87206e622bca075b3fa358b5c173e458a564ac2ca7aa60d7877b56e",
  "studio/controlled-provider.mjs": "ccf787c085080d84652a671c7f23a67be97f60933d3838d42b520ea4f7fe3210",
  "studio/server.mjs": "5e78f75b108ec233be8b83844fc82b3739e0b6bf7fd27ba6ecfcee4fff2e1793",
  "plugins/catalog.mjs": "986b1e55645f347506a8f39bf6c86af152cf9fecde225a1b4ff6be7bcb5452ad",
  "plugins/host.mjs": "f8c541d8016d3cf5f46b6a9823a4c763edb27b9fc9113d45591458f65ca7f469",
  "studio/history-runtime.mjs": "fe06dbf79790c2c0fa73ad0272ed1ec72aaf70cd4dace11b47ea1a0c8a3ebc2d",
  "studio/history-assembly.mjs": "fe64f6b8b29adf075726c65d9f7194b13d7dcf06848999500f4734e8772621e1",
  "studio/summary-assembly.mjs": "898ab39801e6272cc854305ce349d81531285c71341c00f3e7e1b9482a8e19e4",
  "studio/summary-state.mjs": "3198f0eed48f774f0ae349d2f29013818983f249b7accada2a469b0444cef8f4",
  "studio/summary-runtime.mjs": "b6de504e932ca25cb77a1dd636a78d6b9c37ae48063d2df6c7450b338d1f4167",
  "studio/summary-session-store.mjs": "b2780c0a5e57a9e0585adfa838f0f77a14f34347d7f9d97d4c4fc40c1ac3687c",
  "studio/memory-runtime.mjs": "135cf0a630995cef26ca99ef630b697888424b550bbb7bd6467a5452ed4b2954",
  "studio/memory-assembly.mjs": "e32cb564f08efbd2c2ee2ad03c2c677dd7d45a12942c17a15c5d7f1065599cf1",
  "studio/memory-branch-archive.mjs": "ae5d6b9bc047940d47aa795b01df5ad597d58026827e2f15d81f29762623ff4a",
  "studio/memory-session-store.mjs": "2cd578a9d8723e6fe548f1dd00dc8d30f9ce8ad79a03a32ed612e6d1715be7e6",
  "studio/memory-task-state.mjs": "c08c01dbd27f4910254b4ef09365bfc8375c9e3f42f41952825a6752e2c7323f",
  "studio/memory-tasks.mjs": "d4dce0328da26d0f3a72ad3b64ec670b2f3d036ba72996cd15df99d7bc57126e",
  "studio/memory-task-provider.mjs": "d6070c676307ad2cb8761945443cfcd60fbb2de6807e41dee2528a6bc702b4d2",
  "studio/memory-budget.mjs": "09d29ad6230bbe4e40d5d131dd3983c5d77c705d796fd819ff4ec3194a8d10d0",
  "god-card/lib/adapter.mjs": "e5ef0339be873c0f3fd4e1d33ce20d6d1bd2fbc8ec2be0d91764c59a8dac1aa1",
  "god-card/lib/http.mjs": "8733bb9586635165d8e29314ea6dd1702a32899deb90fd5af5491477c671a308",
  "god-card/lib/plugin-runtime.mjs": "0d14a583c815cab4458f1d2c7e6b07bbd9907842f643ca47703b7e121f059f24",
  "studio/card-session-store.mjs": "0e97151e1bae1b66c088a104ca89d73616e0accd818936ff4f1356a1dc00818d",
  "studio/memory-http.mjs": "8611921c2718194bb7ac465cefb0f1715a738f7339255231ab137f39ab7f5e39"
};
const mainBefore={
  "card-replica/lib/store.mjs": "a14fd94f2e86f091d1088a256817c7724f3a4965d9effdaa3963ac12aa624bec",
  "studio/branch-archive.mjs": "f6afcf958ba27c795fe069a7a1ce8da828a3d8b231a76de46b3a3a2964081a5b",
  "studio/runtime-binding.mjs": "ab90568f3825373888f66b0757990507fe9680fde6a1db25d595fd85f3c59b40",
  "studio/model-runtime.mjs": "f9c48f349527caa42952fe452996214fa333db09ab90e2b2ff01e90a043338ba",
  "studio/model-session-store.mjs": "971ab21ae87206e622bca075b3fa358b5c173e458a564ac2ca7aa60d7877b56e",
  "studio/server.mjs": "56b6b047c0eabc3ce04cd95851997208ad9173abc7d5833d7b6656b93e3ffb07",
  "plugins/catalog.mjs": "986b1e55645f347506a8f39bf6c86af152cf9fecde225a1b4ff6be7bcb5452ad",
  "plugins/host.mjs": "f8c541d8016d3cf5f46b6a9823a4c763edb27b9fc9113d45591458f65ca7f469",
  "studio/history-assembly.mjs": "25c43ddcc2f1e4c1cc60e6b4200c5f6295de3e2e7b5abcc862f3417af12c44c8",
  "studio/summary-assembly.mjs": "279c5f71f23ef5fd66703f3e0d796a61ec32af43d79df4950dcad9b2fad62e93",
  "studio/summary-state.mjs": "3198f0eed48f774f0ae349d2f29013818983f249b7accada2a469b0444cef8f4",
  "studio/default-summary-v2.mjs": "2098746629f630883a825e1cd5adfc3f4d207cd6dbd15970863114131b63ccf2",
  "studio/memory-assembly.mjs": "98974c35513e7970cda4cd7eae3927105ba59ae63a116aa0d94f065fc554951d",
  "studio/memory-task-state.mjs": "c08c01dbd27f4910254b4ef09365bfc8375c9e3f42f41952825a6752e2c7323f"
};
const mainBases=["ccfd1d747263399fe61c6dad99481d0406623a7a5ba65204117acf7d5b79956b", "b53021718dc0354715cee0bba8138a95586c8d88ea08d3660cd46cac6442b48b", "8828138f0febb3be35eb6e5a6aa6536d968fb794cccd5372aec48fe154d465dc", "c1c4fc4f7332955f144c45b2d72f4f5e852e99043772a26601e138dd94438ace", "69b8c5fbe3b02a5fb20cfa76631646e8b1a0e6b975bf190fb6bc4e37a1f080f1"];
const oldBases=["05d0d34f2604bbcd547ddad89e183742c55bc769bd99cb0be81fbe34688895ca", "e31d4ff9cd656eb8abf8cb766ecc223b9e7b3548c0b7f179cabe0f2aebae0bbf", "f2a62c8c08b197bae80eb3cf0b76b7b7d013067acd092b417754b961f4e3ae18", "8b9ea914d2c847521c6a69220b3b5cc5bbb595b94ee803e402789269a0e86fed", "923d2b170eb3246da01ca9508000e0858ecd6ee9107de05683fc96eeaff37875"];
const newBases=[earthRuntime,modelRuntime,historyRuntime,summaryRuntime,memoryRuntime].map(r=>r.hash);
const current=Object.fromEntries(await Promise.all([...new Set([...Object.keys(before),...Object.keys(mainBefore),'studio/output-edits.mjs'])].map(async p=>[p,sha(await readFile(new URL('../'+p,import.meta.url)))])));
export function preserveOutputEditRuntime(store){
 const original=store.status.bind(store);
 store.status=s=>{
  const result=original(s);
  if(result.code!=='RUNTIME_VERSION_MISMATCH')return result;
  for(const [previous,bases] of [[mainBefore,mainBases],[before,oldBases]]){
   const descriptor=structuredClone(s.runtime.descriptor);
   let changed=false;
   for(const artifacts of [descriptor.artifacts,descriptor.hostArtifacts,descriptor.content?.cardArtifacts]){
    if(!artifacts)continue;
    for(const [path,old] of Object.entries(previous)){
     if(artifacts[path]===old){artifacts[path]=current[path];changed=true;}
    }
   }
   for(const key of ['artifacts','hostArtifacts'])if(descriptor[key]&&!Object.hasOwn(descriptor[key],'studio/output-edits.mjs'))descriptor[key]['studio/output-edits.mjs']=current['studio/output-edits.mjs'];
   const index=bases.indexOf(descriptor.baseRuntimeHash);
   if(index>=0)descriptor.baseRuntimeHash=newBases[index];
   if(!changed)continue;
   const probe=original({...s,runtime:{...s.runtime,descriptor,hash:sha(canonical(descriptor))}});
   if(probe.compatible)return {compatible:true,code:null,hash:s.runtime.hash};
  }
  return result;
 };
}
