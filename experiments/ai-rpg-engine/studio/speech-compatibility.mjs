import {beforeVoiceLibrary} from './voice-library-compatibility.mjs';
// Exact pre-speech digests from d9dd7094. Only optional speech plumbing changes.
// Compare complete descriptors; never rewrite saves, prompts or receipts.
export const beforeSpeech={
  "studio/controlled-provider.mjs": "8f55c5088b8c7a28dcaf0d6e4610805b68620e4512d1a0ebc447583c331873f9",
  "plugins/catalog.mjs": "a6f955f96c1417efe9f3e2c334d045f0b4376b53bc7eaa04d773818c13072486",
  "plugins/host.mjs": "f5ecc40c20c4482a564b72d1836dfac3bb1f4e2555169e92862132f9b92cad27",
  "studio/server.mjs": "dcbb09be866cacec44920f5fa31396431de211ff6b0140dedc695e287ffc5400",
  "studio/generation-cards.mjs": "b6ada6f27ef3e773cdca6f2d72107f3bc1b78765f821e9ab4e3338a6dbf74240",
  "god-card/lib/http.mjs": "9c939a10660031457c43dde262e901f592b2fa64d4b71e683395c30996dabe6e",
  "xingjian-card/host.mjs": "d6ec1329f4e4d6baab1d1250c7ee694d14a355fc8c9ea0be383e3bc70d05c4bb",
  "jinyong-card/host.mjs": "da5e047f44126c75a59b1905640c8765e483a1105c337cbb0bd9fc07226ec910",
  "medieval-card/host.mjs": "a1d8200ea704d7ad9f1bfaffd8516f72f30270113df4893d995be88ba2504b2d",
  "studio/memory-tasks.mjs": "7881b79feea6c7e252145ab59917668668eb7c9a5fe9661a23c12199f0a06aaf",
  "studio/summary-tasks.mjs": "8eb82f8860831bab1513fb1ba9dd868b166259ba4405c9bab772ac0cba6bf7e9",
  "studio/memory-session-store.mjs": "d138b795386392c74437ebbd6e6bf3d811b685783b84bc61e70b522431710cdd"
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

// Exact pre-speech fingerprints from main fefc14b0, including failed-turn recovery.
export const beforeSpeechMain={
  "studio/controlled-provider.mjs": "8f55c5088b8c7a28dcaf0d6e4610805b68620e4512d1a0ebc447583c331873f9",
  "plugins/catalog.mjs": "a6f955f96c1417efe9f3e2c334d045f0b4376b53bc7eaa04d773818c13072486",
  "plugins/host.mjs": "f5ecc40c20c4482a564b72d1836dfac3bb1f4e2555169e92862132f9b92cad27",
  "studio/server.mjs": "c13abec69cb6ea1a6f21fbdd7d11dd35831341b586bf32003920efcfa4eba351",
  "studio/generation-cards.mjs": "b6ada6f27ef3e773cdca6f2d72107f3bc1b78765f821e9ab4e3338a6dbf74240",
  "god-card/lib/http.mjs": "9c939a10660031457c43dde262e901f592b2fa64d4b71e683395c30996dabe6e",
  "xingjian-card/host.mjs": "d6ec1329f4e4d6baab1d1250c7ee694d14a355fc8c9ea0be383e3bc70d05c4bb",
  "jinyong-card/host.mjs": "da5e047f44126c75a59b1905640c8765e483a1105c337cbb0bd9fc07226ec910",
  "medieval-card/host.mjs": "a1d8200ea704d7ad9f1bfaffd8516f72f30270113df4893d995be88ba2504b2d",
  "harry-potter-card/host.mjs": "e2e5f908d1ba9e560d570be93f51af02e1434df158d6651458e9bfce422f2acf"
};
export const beforeSpeechMainBases=["afd2204a6fa78049f37ce2d02de7a06bdb03e0cc608a0ab92e1ce44c08e795e6", "a1bf4555ec41de75ce577419abac97a04329198cd2482950171af5eb6772f7c5", "2415e3ff54839314e555a3ac09a65f09b05e9ab32c7d21fbf2939e1894bf697c", "ec256a7c47db1ec2928d8293c2ceb61323d02b3c0901ffad6a6d818f3c727cb8", "929a6d8f4485ea882d2d6e1516a783323f3a696ebbbc9cba880b5a6b72b7d8ce"];
export function beforeSpeechMainDescriptor(descriptor,adapterPath){
 const next=structuredClone(descriptor);
 for(const key of ["artifacts","hostArtifacts"])if(next[key])for(const path of Object.keys(next[key]))if(beforeSpeechMain[path])next[key][path]=beforeSpeechMain[path];
 if(adapterPath){next.adapterHash=beforeSpeechMain[adapterPath];next.transportArtifacts["host.mjs"]=beforeSpeechMain[adapterPath];}
 return next;
}

// Exact reviewed c9db3034 fingerprints before the main synchronization.
export const beforeSpeechShop={
  "studio/controlled-provider.mjs": "f4ef3cb449a164eeedf34e7ce9c93d03662e700b82083bbaf6c73a94e33c3c28",
  "plugins/catalog.mjs": "21c4a257aa45ba3c4ebbe2d4c5dd4a3e7b6d998635b6275388f8ada0315c7832",
  "plugins/host.mjs": "bd43d839c5023c8f06fe068fc434452897ed798558bb1333798cf331c6e5dabd",
  "studio/server.mjs": "4866d319abda899b1eedd01853ab1bc127a9f856e86c478bceedaa5ae34f149a",
  "studio/generation-cards.mjs": "b6ada6f27ef3e773cdca6f2d72107f3bc1b78765f821e9ab4e3338a6dbf74240",
  "god-card/lib/http.mjs": "9c939a10660031457c43dde262e901f592b2fa64d4b71e683395c30996dabe6e",
  "xingjian-card/host.mjs": "d6ec1329f4e4d6baab1d1250c7ee694d14a355fc8c9ea0be383e3bc70d05c4bb",
  "jinyong-card/host.mjs": "da5e047f44126c75a59b1905640c8765e483a1105c337cbb0bd9fc07226ec910",
  "medieval-card/host.mjs": "a1d8200ea704d7ad9f1bfaffd8516f72f30270113df4893d995be88ba2504b2d",
  "harry-potter-card/host.mjs": "e2e5f908d1ba9e560d570be93f51af02e1434df158d6651458e9bfce422f2acf"
};
export const beforeSpeechShopBases=["afd2204a6fa78049f37ce2d02de7a06bdb03e0cc608a0ab92e1ce44c08e795e6", "14b31129e4792063017ffbf61105c2c257feebbb30d4fc35496d59c4d07698cb", "55a13599de28f33e1567ae6b86f6c4bb35a2a07a6d4038f91232af027524a8cf", "cd46068710b2b8ea06635feb594e3ae1192fa17501f6a17d17dde673244003b6", "c053b940b0925ae4b54c62b6380c237477ed6ac186f0c98045c067954ca4278b"];

// Exact reviewed 4a32fbe2 fingerprints before the main synchronization.
export const beforeSpeechRelease={
  "studio/controlled-provider.mjs": "8f55c5088b8c7a28dcaf0d6e4610805b68620e4512d1a0ebc447583c331873f9",
  "plugins/catalog.mjs": "3cfb77bb3d45593de0771ffd8ec8bfb0b36cab04ed8143212f1496635e9b109d",
  "plugins/host.mjs": "2162c9ce812739ecefb2777011a86c3a7c368666279cb9df13480031e7d0b369",
  "studio/server.mjs": "b1e2d680d0ec7f13d0a1525dc3a635cedbfcccebb8e61d55c55684ea22c6a957",
  "studio/generation-cards.mjs": "9eb3fad904be949692cc78d6d0d036ae00f25f09fb2b8b2bd801006cc4298fb8",
  "god-card/lib/http.mjs": "9c0596aecc582bd7477532e4a00be92e4029fec520efbe468119252a54b84c8d",
  "xingjian-card/host.mjs": "244cfe5b70e33c07ecc41ec1b29a8b6da817372ebdee57f9c7ce2760e4f484e1",
  "jinyong-card/host.mjs": "73fa1c2d0842164ab806f0343b1d8ba09e9d40809b3b152d7309ecbb9ad2f8c7",
  "medieval-card/host.mjs": "f563e2dd59c2c39ccb25118ab6a0737239ff6b74a90c375c0a0e4c97221e5ca0",
  "harry-potter-card/host.mjs": "63ce4e8e55848bdbf4f16992d8bc33cab1176d6e679d52c556f4bb2f4b50da05"
};
export const beforeSpeechReleaseBases=["afd2204a6fa78049f37ce2d02de7a06bdb03e0cc608a0ab92e1ce44c08e795e6", "a1bf4555ec41de75ce577419abac97a04329198cd2482950171af5eb6772f7c5", "63b6ed8e5096d835aa2318b8d27b2a92cf9fc4ffc960f823ba8fbd215d8d8490", "3e7bb29a8af25a68d4e6cf3738d9154ab946b2df0476405f53ad915ec5cf0016", "2c2a1931d5f185ba0929a92f68cbac7545612708439312a374d2ffbb60b75150"];

export function speechPredecessorDescriptors(descriptor){
 return [beforeVoiceLibrary,beforeSpeech,beforeSpeechMain,beforeSpeechShop,beforeSpeechRelease,beforeSpeechUpgrade,beforeSpeechSynced].map(previous=>{
  const next=structuredClone(descriptor);
  for(const key of ["artifacts","hostArtifacts"])if(next[key])for(const path of Object.keys(next[key]))if(previous[path])next[key][path]=previous[path];
  return next;
 });
}

// Exact reviewed 0a28b830 fingerprints before plugin-upgrade integration.
export const beforeSpeechUpgrade={
  "studio/controlled-provider.mjs": "f4ef3cb449a164eeedf34e7ce9c93d03662e700b82083bbaf6c73a94e33c3c28",
  "plugins/catalog.mjs": "21c4a257aa45ba3c4ebbe2d4c5dd4a3e7b6d998635b6275388f8ada0315c7832",
  "plugins/host.mjs": "6e5a7ec5aeb85463d25fdb33208e6b155a6ae9f685750bac9157befd1821be95",
  "studio/server.mjs": "021e337eedf8ba63e9144230036e4ebd5ff4b71c2527c0cdc9d21a08ce672cd2",
  "studio/generation-cards.mjs": "49097a94fce8d1c22fb9590006634499030d50146d2c1b1f64bea4cb419d765a",
  "god-card/lib/http.mjs": "9c939a10660031457c43dde262e901f592b2fa64d4b71e683395c30996dabe6e",
  "xingjian-card/host.mjs": "d6ec1329f4e4d6baab1d1250c7ee694d14a355fc8c9ea0be383e3bc70d05c4bb",
  "jinyong-card/host.mjs": "da5e047f44126c75a59b1905640c8765e483a1105c337cbb0bd9fc07226ec910",
  "medieval-card/host.mjs": "a1d8200ea704d7ad9f1bfaffd8516f72f30270113df4893d995be88ba2504b2d",
  "harry-potter-card/host.mjs": "e2e5f908d1ba9e560d570be93f51af02e1434df158d6651458e9bfce422f2acf"
};
export const beforeSpeechUpgradeBases=["afd2204a6fa78049f37ce2d02de7a06bdb03e0cc608a0ab92e1ce44c08e795e6", "14b31129e4792063017ffbf61105c2c257feebbb30d4fc35496d59c4d07698cb", "3fb9da4145cd7ecffa780c860d85704eda4485706f8ccd3020411167f06ce0de", "c24c303aeca1513f2a33c5594a60b5a56f8d7b32bf2fa78b69db3f207ed8764b", "7536c60acc1a830fcaff6dee2f0232ee1b806ae7afc3278c5edd7f5edd1f8385"];

// Exact reviewed 9e7ac9f3 fingerprints before plugin-upgrade integration.
export const beforeSpeechSynced={
  "studio/controlled-provider.mjs": "f4ef3cb449a164eeedf34e7ce9c93d03662e700b82083bbaf6c73a94e33c3c28",
  "plugins/catalog.mjs": "5a07cd0f345031b1b5750d881c0ce00f1a8738b58f4c763f7cb27bdeda4b0a83",
  "plugins/host.mjs": "77c8178caf7aeca26f65ec0f670d71e96067e813993658d3f082d3d4822d6011",
  "studio/server.mjs": "85c34c235643e7f9a11ad3c221f478a0fcde6703f7c3d266ece43d951054a416",
  "studio/generation-cards.mjs": "44bff604910d720d5fe31975fb9f1bc10ed964352d0e78838c6bf23f1526327f",
  "god-card/lib/http.mjs": "9c0596aecc582bd7477532e4a00be92e4029fec520efbe468119252a54b84c8d",
  "xingjian-card/host.mjs": "244cfe5b70e33c07ecc41ec1b29a8b6da817372ebdee57f9c7ce2760e4f484e1",
  "jinyong-card/host.mjs": "73fa1c2d0842164ab806f0343b1d8ba09e9d40809b3b152d7309ecbb9ad2f8c7",
  "medieval-card/host.mjs": "f563e2dd59c2c39ccb25118ab6a0737239ff6b74a90c375c0a0e4c97221e5ca0",
  "harry-potter-card/host.mjs": "63ce4e8e55848bdbf4f16992d8bc33cab1176d6e679d52c556f4bb2f4b50da05"
};
export const beforeSpeechSyncedBases=["afd2204a6fa78049f37ce2d02de7a06bdb03e0cc608a0ab92e1ce44c08e795e6", "14b31129e4792063017ffbf61105c2c257feebbb30d4fc35496d59c4d07698cb", "74291707614a2068076b6a9941ad4a72289a3fea4fb646dfa55c667c7e456a32", "9b547b805b5604274a5d4dc7c18952e689e30fcca0748b8fab7f02d94f73ec0c", "ba3c4bfbcb2f3dc3824aa9beefc6a9da91ff3116a76a752f3e23f2f756f704d5"];
