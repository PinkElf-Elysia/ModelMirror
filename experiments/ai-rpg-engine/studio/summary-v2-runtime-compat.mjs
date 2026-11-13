import {beforeFreeOpening} from './free-opening-runtime-compat.mjs';
// Byte hashes of the reviewed pre-v2 host. Only the files changed by this
// release may differ when admitting old sessions. Stored bindings stay intact.
const previous=Object.freeze({
 'studio/server.mjs':'5e78f75b108ec233be8b83844fc82b3739e0b6bf7fd27ba6ecfcee4fff2e1793',
 'studio/history-runtime.mjs':'fe06dbf79790c2c0fa73ad0272ed1ec72aaf70cd4dace11b47ea1a0c8a3ebc2d',
 'studio/summary-assembly.mjs':'898ab39801e6272cc854305ce349d81531285c71341c00f3e7e1b9482a8e19e4',
 'studio/summary-session-store.mjs':'b2780c0a5e57a9e0585adfa838f0f77a14f34347d7f9d97d4c4fc40c1ac3687c',
 'studio/summary-runtime.mjs':'b6de504e932ca25cb77a1dd636a78d6b9c37ae48063d2df6c7450b338d1f4167',
 'studio/memory-assembly.mjs':'e32cb564f08efbd2c2ee2ad03c2c677dd7d45a12942c17a15c5d7f1065599cf1',
 'studio/memory-session-store.mjs':'2cd578a9d8723e6fe548f1dd00dc8d30f9ce8ad79a03a32ed612e6d1715be7e6',
 'studio/memory-runtime.mjs':'135cf0a630995cef26ca99ef630b697888424b550bbb7bd6467a5452ed4b2954',
 'studio/memory-tasks.mjs':'d4dce0328da26d0f3a72ad3b64ec670b2f3d036ba72996cd15df99d7bc57126e',
 'studio/memory-branch-archive.mjs':'ae5d6b9bc047940d47aa795b01df5ad597d58026827e2f15d81f29762623ff4a',
 'studio/memory-http.mjs':'8611921c2718194bb7ac465cefb0f1715a738f7339255231ab137f39ab7f5e39',
 'god-card/lib/http.mjs':'8733bb9586635165d8e29314ea6dd1702a32899deb90fd5af5491477c671a308',
 'god-card/lib/plugin-runtime.mjs':'0d14a583c815cab4458f1d2c7e6b07bbd9907842f643ca47703b7e121f059f24',
 'god-card/lib/real-profile.mjs':'f0e7f38962c387707c2854a68f488ab19da18d6116e27e7c7ee66e89120bf776'
});
export const summaryV2Artifacts=['studio/default-summary-v2.mjs','studio/summary-v2-runtime-compat.mjs','plugins/auto-summary-v2.mjs'];
export function beforeSummaryV2(descriptor){
 descriptor=beforeFreeOpening(descriptor);
 const hostArtifacts=Object.fromEntries(Object.entries(descriptor.hostArtifacts)
  .filter(([path])=>!summaryV2Artifacts.includes(path)).map(([path,hash])=>[path,previous[path]||hash]));
 return {...descriptor,hostArtifacts};
}
