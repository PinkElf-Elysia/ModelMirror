import test from 'node:test';
import assert from 'node:assert/strict';
import {zipSync} from 'fflate';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {inspectCharx} from './charx.mjs';
import {blankCard} from './card.mjs';
import {placeholder,chunk,pngChunks} from './png.mjs';
import {startAuthoringServer} from '../authoring/server.mjs';
import {sha256} from '../authoring/contracts.mjs';
import {charxLimits} from './charx-archive.mjs';

const path='assets/icon/images/portrait.png', png=placeholder();
const icon={type:'icon',name:'main',uri:'embeded://'+path,ext:'png'};
const card={...blankCard(),spec:'chara_card_v3',spec_version:'3.0'};
Object.assign(card.data,{name:'纸灯记录员',group_only_greetings:[],extensions:{fixture:{keep:true}}});
const pack=(assets=[icon],files={[path]:png},options={})=>Buffer.from(zipSync({
 'card.json':Buffer.from(JSON.stringify({...card,data:{...card.data,assets}})),...files
},options));

test('classifies case-sensitive references, shared files, defaults and offline remote URIs',async()=>{
 const info=await inspectCharx(pack([icon,{...icon,type:'background'},{...icon,type:'emotion',uri:'https://fixture.invalid/face.png'},{...icon,type:'user_icon',uri:'ccdefault:'}]));
 assert.equal(info.classification,'images');assert.equal(info.name,card.data.name);
 const row=info.members.find(r=>r.path===path);assert.equal(row.uses.length,2);assert.equal(row.uses[0].main,true);assert.equal(row.width,1);assert.equal(row.size,png.length);
 assert.equal(info.members.find(r=>r.path.startsWith('https')).status,'远程引用，仅保留');
});
test('mixed packages, unreferenced files, ambiguous icons and missing assets retain only original',async()=>{
 for(const bytes of [pack([icon],{[path]:png,'docs/note.txt':Buffer.from('fixture')}),pack([icon,{...icon}],{[path]:png}),
  pack([{...icon,uri:icon.uri.toUpperCase()}]),pack([{...icon,uri:icon.uri.replace('portrait','Portrait')}]),
  pack([{...icon,ext:'webp'}]),pack([]),pack([{...icon,uri:'data:image/png;base64,AA=='}])]){
   const report=await inspectCharx(bytes);assert.equal(report.classification,'original-only');assert.ok(report.diagnostics.length);
 }
});
test('blocks broken envelopes, mismatched headers, unsafe paths and invalid images',async()=>{
 const valid=pack(), central=valid.indexOf(Buffer.from('504b0102','hex'));
 for(const [at,value] of [[central+8,1],[central+10,12],[central+38+2,0xa000],[central+16,0],[6,4],[valid.length-12,257]]){
  const broken=Buffer.from(valid);broken.writeUInt16LE(value,at);await assert.rejects(inspectCharx(broken),{code:'CHARACTER_CHARX_INVALID'});
 }
 const badCRC=Buffer.from(valid);badCRC.writeUInt32LE(1,14);badCRC.writeUInt32LE(1,central+16);
 const rejected=[badCRC,valid.subarray(0,-4),pack([icon],{'../escape.png':png}),pack([icon],{[path]:png,[path.toUpperCase()]:png}),
  pack([{...icon,uri:'embeded://../escape'}]),pack([icon],{[path]:Buffer.from('not png')}),
  pack([icon],{'unused/script':png,'unused':png}),Buffer.from(zipSync({'z.txt':png}))];
 for(const bytes of rejected)await assert.rejects(inspectCharx(bytes),{code:'CHARACTER_CHARX_INVALID'});
});
test('decompressed size, declared size, image dimensions and animation checks',async()=>{
 const pngHeader=Buffer.from(pngChunks(png)[0].data);pngHeader.writeUInt32BE(40_000_001);
 const huge=Buffer.concat([png.subarray(0,8),chunk('IHDR',pngHeader),png.subarray(33)]);
 await assert.rejects(inspectCharx(pack([icon],{[path]:huge})),{code:'CHARACTER_CHARX_INVALID'});
 await assert.rejects(inspectCharx(pack([],{x:Buffer.alloc(charxLimits.file+1)})),{code:'CHARACTER_CHARX_INVALID'});
 const wrong=pack();const cd=wrong.indexOf(Buffer.from('504b0102','hex'));
 wrong.writeUInt32LE(1,22);wrong.writeUInt32LE(1,cd+24);
 await assert.rejects(inspectCharx(wrong),{code:'CHARACTER_CHARX_INVALID'});
 const animated=Buffer.concat([png.subarray(0,33),chunk('acTL',Buffer.from('0000000100000000','hex')),png.subarray(33)]);
 assert.equal((await inspectCharx(pack([icon],{[path]:animated}))).classification,'original-only');
});
test('inspection is read-only; explicit original retention verifies content and survives restart',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'r6a-charx-'));
 let server=await startAuthoringServer({directory,creatorExperience:true});t.after(()=>server.close());
 const endpoint='/rpg-app/api/authoring/characters';
 const get=route=>fetch(server.origin+endpoint+route);
 const post=(route,input)=>fetch(server.origin+endpoint+route,{method:'POST',headers:{origin:server.origin,'content-type':'application/json','x-authoring-request':'1'},body:JSON.stringify(input)});
 const bytes=pack([icon],{[path]:png,'other/notes.txt':Buffer.from('中性夹具')}),base64=bytes.toString('base64'),hash=sha256(bytes);
 const report=await (await post('/imports/inspect',{base64,selection:null})).json();
 assert.equal(report.source.sha256,hash);assert.equal(report.classification,'original-only');
 assert.deepEqual(await (await get('/works')).json(),[]);assert.deepEqual(await (await get('/charx/originals')).json(),[]);
 const uri='/charx/original?hash='+hash;
 assert.equal((await get(uri)).ok,false);
 const retain={base64,expectedHash:hash,fileName:'中性夹具.charx'};
 assert.equal((await post('/charx/originals',{...retain,expectedHash:'0'.repeat(64)})).ok,false);
 assert.equal((await post('/imports',{...retain,selection:null,operationId:'fixture-import',origin:''})).ok,false);
 const damaged=await post('/imports/inspect',{base64:Buffer.from('corrupt').toString('base64'),selection:null,formatHint:'charx'});
 assert.equal((await damaged.json()).error.code,'CHARACTER_CHARX_INVALID');
 assert.deepEqual(await Promise.all([1,2].map(async()=> (await post('/charx/originals',retain)).json())),[{hash},{hash}]);
 await server.close();server=await startAuthoringServer({directory,creatorExperience:true});
 assert.equal((await (await get('/charx/originals')).json()).length,1);
 const original=await get(uri);assert.match(original.headers.get('content-disposition'),/attachment/);
 assert.deepEqual(Buffer.from(await original.arrayBuffer()),bytes);
 assert.deepEqual(await (await get('/works')).json(),[]);
 const blocked=await fetch(server.origin+endpoint+uri,{headers:{origin:'https://example.invalid'}});assert.equal(blocked.status,403);
});
