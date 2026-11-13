import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';
import {start} from './server.mjs';
import {createArchiveHost} from '../xingjian-card/host.mjs';
import {createJinyongHost} from '../jinyong-card/host.mjs';
import {createMedievalHost} from '../medieval-card/host.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';
const fixture=JSON.parse(await readFile(new URL('./speech-baseline.fixture.json',import.meta.url),'utf8'));
const cases=(store,state,label)=>{
 const before=canonical(state);assert.equal(store.status(state).compatible,true,label+' old save');
 const corrupt=structuredClone(state);corrupt.runtime.hash='0'.repeat(64);assert.equal(store.status(corrupt).compatible,false,label+' hash tamper');
 const changed=structuredClone(state);changed.characterText+='changed';assert.equal(store.status(changed).compatible,false,label+' setup tamper');
 const drift=structuredClone(state);drift.runtime.descriptor.systemHash='unreviewed drift';drift.runtime.hash=sha(canonical(drift.runtime.descriptor));assert.equal(store.status(drift).compatible,false,label+' unrelated runtime drift');
 assert.equal(canonical(state),before,label+' save unchanged');
};
test('baseline d9dd7094 ordinary and memory-enabled Earth/God saves remain readable without rebinding',async()=>{
 for(const memory of [false,true]){
  const host=await start({directory:await mkdtemp(join(tmpdir(),'speech-compat-')),port:0,godCard:true,godPlugins:memory,rollingSummary:memory,memoryPalace:memory,historyWindow:memory,autoSummaryV2:memory});
  try{cases(host.earth,fixture.sessions[memory?'earthMemory':'earth'],'earth '+memory);cases(host.god,fixture.sessions[memory?'godMemory':'god'],'god '+memory);}finally{await new Promise(r=>host.server.close(r));await host.plugins.close();}
 }
});
test('baseline archive saves keep full descriptor checks for optional speech integration',async()=>{
 for(const id of ['cultivation','xingjian','jinyong','medieval']){
  const factory=id==='jinyong'?createJinyongHost:id==='medieval'?createMedievalHost:createArchiveHost;
  const h=await factory(await mkdtemp(join(tmpdir(),'speech-archive-')),null,{cardId:id,name:'测试',system:'测试世界',pluginFeatures:{speech:true}});
  try{cases(h.store,fixture.sessions[id],id);const c=await h.plugins.catalog();assert(c.plugins.some(p=>p.id==='rpg.speech'));}finally{await h.plugins.close();}
 }
});
