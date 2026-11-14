import {readFile} from 'node:fs/promises';
import {runtimePaths as foundationPaths} from '../conversation/runtime.mjs';
import {sha256} from '../../authoring/contracts.mjs';
import {FORMAT,digest} from './contracts.mjs';
export const runtimePaths=[...foundationPaths,'character/group/contracts.mjs','character/group/context.mjs','character/group/runtime.mjs','character/group/store.mjs','character/group/host.mjs','character/group/gateway-receipt.mjs','character/group/gateway-transport.mjs'];
export async function runtimeDescriptor(){const root=new URL('../../',import.meta.url),artifacts=Object.fromEntries(await Promise.all(runtimePaths.map(async p=>[p,sha256(await readFile(new URL(p,root)))])));return {format:FORMAT,artifacts,hash:digest({format:FORMAT,artifacts})};}
