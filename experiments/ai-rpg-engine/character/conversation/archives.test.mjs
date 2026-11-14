import test from 'node:test';import assert from 'node:assert/strict';import {join} from 'node:path';import {fileURLToPath,pathToFileURL} from 'node:url';import {readFile,writeFile,cp,readdir,mkdir,appendFile} from 'node:fs/promises';import {execFileSync} from 'node:child_process';
import {sha256} from '../../authoring/contracts.mjs';import {baselineFixture} from './archive-support.mjs';import {LegacyConversationReader} from './legacy.mjs';
const current=new URL('../../',import.meta.url);
async function hashes(dir){const out={};async function walk(p,rel=''){for(const d of await readdir(p,{withFileTypes:true})){if(d.isDirectory())await walk(join(p,d.name),rel+d.name+'/');else out[rel+d.name]=sha256(await readFile(join(p,d.name)));}}await walk(dir);return out;}
async function open(root,dir){const mod=p=>import(new URL(p,root));const {AuthoringStore}=await mod('authoring/store.mjs'),{blankDocument}=await mod('authoring/contracts.mjs'),{characterDocument}=await mod('character/document.mjs'),{CharacterFixtureProvider}=await mod('character/fixture-provider.mjs'),{CharacterSessionHost}=await mod('character/session.mjs');const works=await AuthoringStore.open(join(dir,'works')),provider=await CharacterFixtureProvider.open(join(dir,'provider')),host=await CharacterSessionHost.open({directory:join(dir,'sessions'),works,provider});return {works,provider,host,blankDocument,characterDocument,async close(){await host.close();await provider.close();await works.close();}};}
for(const baseline of ['af3beb6350350d7021204a5db0118e5a260815e3','0cb019df01642d71a5eef55eeee31d1086ab037d'])test('actual '+baseline.slice(0,8)+' character archive restores and projects without rewriting original',async()=>{
 const {root,directory}=await baselineFixture(baseline),original=join(directory,'original'),copy=join(directory,'copy');await mkdir(original);let f=await open(new URL('../',root),original),closed=false;
 try{
 const d=f.characterDocument(f.blankDocument());Object.assign(d.character.card.data,{name:'Archive fixture',first_mes:'Hello {{user}}'});d.metadata.name='Archive fixture';const w=await f.works.create({operationId:'w',expectedRevision:null,document:d}),v=await f.works.publish(w.workId,{operationId:'v',expectedRevision:0});
 let a=await f.host.create({operationId:'a',reference:{workId:w.workId,versionId:v.result.versionId},openingIndex:0,player:{id:null,name:'Player'}}),b=await f.host.create({operationId:'b',reference:a.binding.reference,openingIndex:0,player:{id:null,name:'Player'}});
 a=await f.host.editMemory(a.id,{expectedRevision:a.revision,operationId:'memory',change:{action:'create',value:{name:'private',content:'ARCHIVE_PRIVATE',enabled:true,match:'any',roles:['user'],keywords:['hello']}}});
 const sentRevision=a.revision;await f.host.send(a.id,{operationId:'old',expectedRevision:sentRevision,input:'hello'});a=await f.host.wait(a.id);await f.close();closed=true;const golden=await hashes(original);await cp(original,copy,{recursive:true});
 f=await open(current,copy);closed=false;assert.deepEqual(await f.host.read(a.id),a);assert.deepEqual(await f.host.read(b.id),b);
 const reader=new LegacyConversationReader(f),projection=await reader.readOwner(a.id);assert.equal(projection.messages.length,3);assert.equal(projection.privateRecords.length,1);
 await f.host.send(a.id,{operationId:'old',expectedRevision:sentRevision,input:'hello'});assert.equal(f.provider.dispatchCount,0);
 await f.host.send(a.id,{operationId:'new',expectedRevision:a.revision,input:'continue'});const done=await f.host.wait(a.id);assert.equal(done.turns,2);assert.deepEqual(done.binding,a.binding);assert.deepEqual(await f.host.read(b.id),b);
 const after=await reader.readOwner(a.id);assert.deepEqual(after.messages.slice(0,3),projection.messages);await f.close();closed=true;assert.deepEqual(await hashes(original),golden);
 // Separate source/data copies prove failure-closed source drift, never edit the original archive/data.
 const drift=join(directory,'drift-source');await cp(fileURLToPath(new URL('../',root)),drift,{recursive:true});
 // Imported modules resolve dependencies from an isolated node_modules copy, without symlinks.
 await cp(fileURLToPath(new URL('node_modules',current)),join(drift,'node_modules'),{recursive:true});await appendFile(join(drift,'character','context.mjs'),'\n// intentional isolated source drift\n');
 const driftData=join(directory,'drift-data');await cp(original,driftData,{recursive:true});f=await open(pathToFileURL(drift+'/'),driftData);closed=false;await assert.rejects(()=>f.host.read(a.id),{code:'CHARACTER_SESSION_CORRUPT'});
 }finally{if(!closed)await f.close();}
});
test('pre-merge main 3fffd5b4 world Offline/Summary/Joint archives retain bindings and reject tampering',async()=>{
 const {directory}=await baselineFixture();const old=await readFile(new URL('../legacy-hosts.test.mjs',import.meta.url),'utf-8');
 const script=join(directory,'worlds.test.mjs'),log=join(directory,'worlds.log');
 const source=old.replace("from './baseline-fixture.mjs'",'from '+JSON.stringify(new URL('./archive-support.mjs',import.meta.url).href)).replace("new URL('../authoring/',import.meta.url)",'new URL('+JSON.stringify(new URL('authoring/',current).href)+')').replaceAll('actual a6563a9b','actual 3fffd5b4');
 await writeFile(script,source);try{const out=execFileSync(process.execPath,['--test',script],{encoding:'utf8',stdio:'pipe'});await writeFile(log,out);}catch(e){await writeFile(log,String(e.stdout||'')+String(e.stderr||''));throw e;}
});
