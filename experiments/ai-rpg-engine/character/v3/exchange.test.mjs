import test from 'node:test';
import assert from 'node:assert/strict';
import {inflateSync,crc32} from 'node:zlib';
import {createRequire} from 'node:module';
import {blankCard,CARD_LIMITS} from '../card.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {placeholder,chunk,decode64,pngChunks} from '../png.mjs';
import {upgradeCard,metadataPanel} from './metadata.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
function fixture(){const c=blankCard();upgradeCard(c,()=>4500);Object.assign(c.data,{name:'渡口向导',nickname:'小桥',creation_date:0,source:['neutral-id'],group_only_greetings:['一\n二'],creator_notes_multilingual:{zh:'作者说明',legacy:'原文'},extensions:{opaque:{深:[0,false]}}});return c;}
function unpack(bytes){const chunks=[];for(let i=8;i<bytes.length;){const n=bytes.readUInt32BE(i),type=bytes.toString('ascii',i+4,i+8),data=bytes.subarray(i+8,i+8+n);assert.equal(crc32(bytes.subarray(i+4,i+8+n)),bytes.readUInt32BE(i+8+n));chunks.push({type,data});i+=12+n;}return chunks;}
test('UTF8 ccv3 independent decode, RGBA pixels, fixed clock and optional backfill',()=>{
 const card=fixture(),original=structuredClone(card),out=exportCard(card,{format:'png',now:()=>123450}).bytes,parts=unpack(out);
 assert.deepEqual(inflateSync(parts.find(c=>c.type==='IDAT').data),Buffer.from([0,68,80,93,255]));
 const meta=parts.filter(c=>c.type==='tEXt');assert.equal(meta.length,1);const [key,b64]=meta[0].data.toString('latin1').split('\0');assert.equal(key,'ccv3');
 const expected=structuredClone(card);expected.data.modification_date=123;assert.deepEqual(JSON.parse(Buffer.from(b64,'base64').toString()),expected);assert.deepEqual(inspectImport(out).card,expected);assert.deepEqual(card,original);
 const round=inspectImport(exportCard(inspectImport(exportCard(card,{now:()=>123450}).bytes).card,{format:'png',now:()=>123450}).bytes);assert.deepEqual(round.card,expected);
 const options={format:'png',version:'v3-backfill'};assert.ok(exportCard(card,options).confirmationRequired);const both=exportCard(card,{...options,acceptLoss:true}).bytes;assert.equal(inspectImport(both).card.spec,card.spec);assert.match(inspectImport(both,{selection:'chara'}).card.data.creator_notes,/V2/);
});
test('animation/embedded asset loss requires consent, raw bytes and URI retained; boundaries',()=>{
 const p=placeholder(),originalPng=Buffer.concat([p.subarray(0,-12),chunk('acTL',Buffer.alloc(8)),chunk('tEXt',Buffer.from('chara-ext-asset_:x\0QQ==')),p.subarray(-12)]),raw=Buffer.from(originalPng),c=fixture();c.data.assets=[{type:'icon',uri:'__asset:x',name:'icon',ext:'png'}];
 const opt={format:'png',originalPng};const before=exportCard(c,opt);assert.equal(before.losses.length,3);assert.equal(before.bytes,undefined);assert.deepEqual(inspectImport(exportCard(c,{...opt,acceptLoss:true}).bytes).card.data.assets,c.data.assets);assert.deepEqual(raw,originalPng);
 assert.equal(decode64(Buffer.alloc(CARD_LIMITS.json).toString('base64')).length,CARD_LIMITS.json);assert.throws(()=>decode64(Buffer.alloc(CARD_LIMITS.json+1).toString('base64')),/FILE_SIZE/);
 const pad=n=>Buffer.concat([p.subarray(0,-12),chunk('ruSt',Buffer.alloc(n-p.length-12)),p.subarray(-12)]);
 assert.ok(pngChunks(pad(CARD_LIMITS.png)).length);assert.throws(()=>pngChunks(pad(CARD_LIMITS.png+1)),/FILE_SIZE/);assert.throws(()=>exportCard(c,{format:'png',image:pad(CARD_LIMITS.png),acceptLoss:true}),/FILE_SIZE/);
});
test('editor preserves unknown languages and dates, edits and reorders multiline group greetings',()=>{
 const dom=new JSDOM('<main></main>'),c=fixture(),m=dom.window.document.querySelector('main');metadataPanel(m,c,()=>{},false);
 const click=s=>[...m.querySelectorAll('button')].find(b=>b.textContent===s).click();const fill=(s,v)=>{const e=[...m.querySelectorAll('label')].find(l=>l.firstChild.textContent===s).lastChild;e.value=v;e.dispatchEvent(new dom.window.Event('input'));};
 fill('角色昵称','');fill('添加说明语言','ja');click('添加语言');fill('作者说明 · ja','甲\n乙');click('添加群开场');fill('群开场 2','欢迎');click('上移群开场 2');assert.deepEqual(c.data.group_only_greetings,['欢迎','一\n二']);assert.equal(c.data.creator_notes_multilingual.legacy,'原文');assert.equal(c.data.creator_notes_multilingual.ja,'甲\n乙');assert.equal(c.data.creation_date,0);dom.window.close();
});