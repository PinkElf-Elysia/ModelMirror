// Exact source bytes deployed before optional, editable openings. Reconstruct
// only these reviewed descriptors; never rewrite saved bindings or relax any
// author prompt, model, setup, request or plugin-grant integrity check.
const previous=Object.freeze({
 'card-replica/lib/assembly.mjs':'7507921b7c15695d62a71e65acc4f7bf5f8d8083a9812b855f17bd5585905bed',
 'studio/memory-session-store.mjs':'9f6526b7aa5b2cd805c83b76577aeffebe84332792856f159f190d62a45f1dfe',
 'studio/history-assembly.mjs':'fe64f6b8b29adf075726c65d9f7194b13d7dcf06848999500f4734e8772621e1',
 'studio/default-summary-v2.mjs':'6d400a68b144c3cf418f6a7894f96c05bd982c2ec885989b45e39539caa39051',
 'studio/summary-v2-runtime-compat.mjs':'c3c0aa8c75155dba62a7c50d9f84204ad3cc4379f6ed658579273323ee8363da',
 'studio/history-runtime.mjs':'8c2482b6561b59c1c5b8d4e6d67be1f1f93834ad672b67ed9d597c763bfcb933',
 'studio/summary-runtime.mjs':'15f0d3752184712ba1bb1438b52d2bd538b56333dca73a3283a4d970f8068b74',
 'studio/memory-runtime.mjs':'c2585cfd0d3f21519ee7f9560d0a739b2d219e8de827d929c54949529e9b9801',
 'god-card/lib/adapter.mjs':'e5ef0339be873c0f3fd4e1d33ce20d6d1bd2fbc8ec2be0d91764c59a8dac1aa1',
 'god-card/lib/plugin-runtime.mjs':'0ed6f7596034baa6ffba7ec11dc87e66b771d2e979b3b6fce284597027b9e2b4',
 'god-card/lib/real-profile.mjs':'56b8287ebe64c4862db00e33408ccbaa29899cc1ac75dbd87d65d470bd211348'
});
export const freeOpeningArtifact='studio/free-opening-runtime-compat.mjs';
const restore=artifacts=>Object.fromEntries(Object.entries(artifacts)
 .filter(([path])=>path!==freeOpeningArtifact).map(([path,hash])=>[path,previous[path]||hash]));
export function beforeFreeOpening(descriptor){
 const result={...descriptor};
 if(descriptor.hostArtifacts)result.hostArtifacts=restore(descriptor.hostArtifacts);
 if(descriptor.content?.cardArtifacts)result.content={...descriptor.content,cardArtifacts:restore(descriptor.content.cardArtifacts)};
 return result;
}
