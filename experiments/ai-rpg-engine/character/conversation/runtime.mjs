import {readFile} from 'node:fs/promises';
import {digest,FORMAT} from './contracts.mjs';import {sha256} from '../../authoring/contracts.mjs';
// New host owns this descriptor; it is never added to an old host fingerprint.
export const runtimePaths=Object.freeze([
 'character/conversation/contracts.mjs','character/conversation/store.mjs','character/conversation/runtime.mjs',
 'authoring/gallery.mjs','authoring/model-control.mjs','authoring/model-plugin.mjs',
 'authoring/contracts.mjs','authoring/store.mjs','authoring/session-storage.mjs','authoring/generation-settings.mjs',
 'authoring/variable-panel.mjs','authoring/variable-template-syntax.mjs','authoring/metadata.mjs','authoring/dialogue.mjs',
 'character/document.mjs','character/card.mjs','character/context.mjs','package-lock.json',
]);
export async function runtimeDescriptor(){
 const root=new URL('../../',import.meta.url),artifacts=Object.fromEntries(await Promise.all(runtimePaths.map(async path=>[path,sha256(await readFile(new URL(path,root)))])));
 const descriptor={format:FORMAT,artifacts};return {...descriptor,hash:digest(descriptor)};
}
