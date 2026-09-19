import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {ModelSessionStore} from './model-session-store.mjs';
import {VersionedSessionStore} from './versioned-store.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {canonical,sha,PLUGIN_ID} from '../plugins/catalog.mjs';
import {randomUUID} from 'node:crypto';

const fixture=process.env.RPG_B3_LEGACY_COPY;
test('actual B5 parent and branch copies remain readable and continue with explicit v1 binding; originals unchanged', {skip:!fixture},async()=>{
 const receipt=JSON.parse(await readFile(join(fixture,'COPY-RECEIPT.json'),'utf8'));
 const root=fileURLToPath(new URL('../.rpg04-work/model-selector-b3/legacy-check/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'l-'));
 for(const row of receipt.files){const data=await readFile(join(fixture,row.file));assert.equal(sha(data),row.sha256);const dest=join(directory,row.file);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,data);}
 let calls=0;const wires=[];const generate=async args=>{calls++;wires.push(args.messages);return '本批离线兼容检查原文';};
 const store=new ModelSessionStore(directory,generate,generate);await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugin-registry'),lookupSession:async id=>{const s=await store.read(id);return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha(s.runtime.hash),busy:false,pending:false};}});
 try{
  const listed=await store.list();assert.equal(listed.length,receipt.files.length);
  for(const row of receipt.files){
   const id=row.file.split('/').at(-1).slice(0,-5),before=await store.read(id);assert.equal(store.status(before).compatible,true);
   const updated=await store.send(id,{input:'仅在副本上模拟继续',requestId:randomUUID(),revision:before.revision});
   assert.equal(canonical(updated.history.slice(0,before.history.length)),canonical(before.history));assert.equal(canonical(updated.runtime),canonical(before.runtime));assert.equal(updated.modelState,undefined);assert.equal(updated.turns.length,before.turns.length+1);
   assert.deepEqual(wires.at(-1).slice(1,-1),before.history);
   const oldReader=new VersionedSessionStore(directory,generate,generate);assert.equal((await oldReader.read(id)).turns.length,updated.turns.length);
  }
  const parent=await store.read(listed.find(s=>!s.parentId).id);
  const change=async action=>{const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===PLUGIN_ID);return plugins.change(action,{pluginId:PLUGIN_ID,operationId:randomUUID(),expectedRegistryRevision:c.revision,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,...(action==='enable'?{sessionId:parent.id,expectedSessionRevision:parent.revision,permissions:p.permissions}:{})});};
  await change('install');await change('enable');const branch=await store.createBranch(parent.id,{operationId:'copied-legacy-branch',expectedSessionRevision:parent.revision,turn:1,name:'仅副本分支'},plugins);assert.equal(branch.session.runtime.format,1);assert.equal(calls,2);
  for(const row of receipt.files){assert.equal(sha(await readFile(join(receipt.source,row.file))),row.sha256);assert.equal(sha(await readFile(join(fixture,row.file))),row.sha256);}
 }finally{await plugins.close();}
});
