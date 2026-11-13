// Exact pre-speech digests from d9dd7094. Only optional speech plumbing changes.
// Compare complete descriptors; never rewrite saves, prompts or receipts.
export const beforeSpeech={
  "plugins/catalog.mjs": "a6f955f96c1417efe9f3e2c334d045f0b4376b53bc7eaa04d773818c13072486",
  "plugins/host.mjs": "f5ecc40c20c4482a564b72d1836dfac3bb1f4e2555169e92862132f9b92cad27",
  "studio/server.mjs": "dcbb09be866cacec44920f5fa31396431de211ff6b0140dedc695e287ffc5400",
  "studio/generation-cards.mjs": "b6ada6f27ef3e773cdca6f2d72107f3bc1b78765f821e9ab4e3338a6dbf74240",
  "god-card/lib/http.mjs": "9c939a10660031457c43dde262e901f592b2fa64d4b71e683395c30996dabe6e",
  "xingjian-card/host.mjs": "d6ec1329f4e4d6baab1d1250c7ee694d14a355fc8c9ea0be383e3bc70d05c4bb",
  "jinyong-card/host.mjs": "da5e047f44126c75a59b1905640c8765e483a1105c337cbb0bd9fc07226ec910",
  "medieval-card/host.mjs": "a1d8200ea704d7ad9f1bfaffd8516f72f30270113df4893d995be88ba2504b2d"
};
export const beforeSpeechBases=["afd2204a6fa78049f37ce2d02de7a06bdb03e0cc608a0ab92e1ce44c08e795e6", "a1bf4555ec41de75ce577419abac97a04329198cd2482950171af5eb6772f7c5", "d16be3eda5e89e6121f92a2585dacadda25396516a7b25c958595f834e96f358", "bd4d7bdf6b571a2610a324b596e1e383054f05379701c55fc510e962aff3b982", "6cb8819f5485043a6fd08cbd676b0ef94708765ecd95b8c5e0238adeec0a030e"];
export function beforeSpeechDescriptor(descriptor,adapterPath){
 const next=structuredClone(descriptor);
 for(const key of ["artifacts","hostArtifacts"]){
  if(next[key])for(const path of Object.keys(next[key]))if(beforeSpeech[path])next[key][path]=beforeSpeech[path];
 }
 if(adapterPath){next.adapterHash=beforeSpeech[adapterPath];if(next.transportArtifacts?.["host.mjs"])next.transportArtifacts["host.mjs"]=beforeSpeech[adapterPath];}
 return next;
}
