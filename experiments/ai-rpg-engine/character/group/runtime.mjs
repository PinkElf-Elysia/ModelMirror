import {readFile} from 'node:fs/promises';
import {runtimePaths as foundationPaths} from '../conversation/runtime.mjs';
import {sha256} from '../../authoring/contracts.mjs';
import {FORMAT,digest} from './contracts.mjs';
export const runtimePaths=[...foundationPaths,'character/group/narration.mjs','character/group/narration-compat.json','character/daily/identity.mjs','character/daily/compatibility.mjs','character/daily/compatibility.json','character/group/contracts.mjs','character/group/context.mjs','character/group/runtime.mjs','character/group/store.mjs','character/group/host.mjs','character/group/gateway-receipt.mjs','character/group/gateway-transport.mjs'];
// Both historical group descriptors are accepted only on the reviewed replacement sources.
export async function acceptsLegacyGroup(hash,artifacts) {
 const bytes=await readFile(new URL('./narration-compat.json',import.meta.url));
 const rule=JSON.parse(bytes);
 const historical=JSON.parse(await readFile(new URL('../daily/compatibility.json',import.meta.url),'utf8')).group.hash;
 const {['character/group/narration-compat.json']:self,...sources}=artifacts;
 return self===sha256(bytes) && (hash===historical || hash===rule.previous && rule.previous==='088eaf5a7054761dfc02d3670c305f514896f60e7c8a4dc049785907130c5a65') && digest(sources)===rule.approved;
}

export async function runtimeDescriptor(){const root=new URL('../../',import.meta.url),artifacts=Object.fromEntries(await Promise.all(runtimePaths.map(async p=>[p,sha256(await readFile(new URL(p,root)))])));return {format:FORMAT,artifacts,hash:digest({format:FORMAT,artifacts})};}
