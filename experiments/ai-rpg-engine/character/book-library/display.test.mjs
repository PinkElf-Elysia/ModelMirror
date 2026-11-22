import assert from 'node:assert/strict';
import test from 'node:test';
import {createRequire} from 'node:module';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {displayText,defaultDisplay,createDisplayPanel} from './display-text.mjs';
import {startDisplayPreview} from './display-preview.mjs';
import {digest} from '../revisions/contracts.mjs';

test('presentation is opt-in, literal, non-recursive and bounded, with exact original on overflow',()=>{
  const original='东岸蓝牌。\\n\\n西岸蓝牌。',p={enabled:true,paragraphs:true,find:'蓝牌',replacement:'蓝牌$&'};
  assert.equal(displayText(original,defaultDisplay()).text,original);
  assert.deepEqual(displayText(original,p),{text:'东岸蓝牌$&。\n\n西岸蓝牌$&。',changed:true});
  assert.equal(displayText('【草稿】甲',{...p,find:'【草稿】',replacement:''}).text,'甲');
  assert.equal(displayText('.*甲',{...p,find:'.*',replacement:'乙'}).text,'乙甲');
  assert.equal(displayText('{"text":"甲\\n\\n乙"}',{...p,find:''}).changed,false);
  const long='甲'.repeat(513),replacement='乙'.repeat(160);
  assert.deepEqual(displayText(long,{...p,find:'甲',replacement}),{text:long,limited:true,changed:false});
});

test('projection is read-only; receipt, future history, refresh and exact candidate are bound to original bytes',async t=>{
  const root=process.env.CHARACTER_R5C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const dir=await mkdtemp(join(root,'display-'));let h=await startDisplayPreview(dir);t.after(()=>h.close());
  const {familyId,branchId,requestId}=h,loc={familyId,branchId};
  const dirBooks=join(dir,'studio/character/independent-books');
  const shelf=()=>readFile(join(dirBooks,'library/records/shelf.json'));
  const receiptBytes=()=>readFile(join(dirBooks,'offline-receipts/records',requestId+'.json'));
  const originalShelf=await shelf(),originalBytes=await receiptBytes(),row=JSON.parse(originalShelf).data.families[familyId];
  const request=row.f.requests[requestId],raw=request.receipt.raw.text,receipt=request.receipt;
  const {messages,members,targetId}=await h.api('view',{...loc,targetId:null}),reply=messages.at(-1);
  const proof=(by,id,opts={})=>h.api('display-receipt',{...loc,targetId,by,id,...opts});
  const a=await proof('request',requestId);assert.equal(a.rawText,raw);assert.equal(a.responseText,receipt.text);
  assert.equal(a.receiptHash,receipt.hash);assert.deepEqual(await proof('message',reply.id),a);
  assert.doesNotMatch(JSON.stringify(a),/prefixHash|operations|authorization/);
  const shown=displayText(reply.content,{enabled:true,paragraphs:true,find:'蓝牌',replacement:'青色标记'});
  assert.ok(shown.text.includes('青色标记。\n\n西岸'));assert.ok(raw.includes('蓝牌。\\n\\n西岸'));
  const preview=async(op,input,messageId=null)=>{
    const intent={branchId,targetId,input,messageId};
    const p=await h.api('preview',{familyId,intent,operationId:op});
    return {p,command:{operationId:op,expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}}};
  };
  const next=await preview('next','继续记录');
  assert.ok(next.p.messages.some(m=>m.role==='assistant'&&JSON.parse(m.content).text===raw));
  assert.ok(!JSON.stringify(next).includes('青色标记'));
  await assert.rejects(proof('request',requestId,{targetId:members[1].id}),/DISPLAY_REFERENCE/);
  await assert.rejects(proof('message',reply.id,{targetId:members[1].id}),/DISPLAY_REFERENCE/);
  await assert.rejects(proof('message',messages[0].id),/DISPLAY_REFERENCE/);
  assert.deepEqual(await receiptBytes(),originalBytes);assert.deepEqual(await shelf(),originalShelf);
  await h.close();h=await startDisplayPreview(dir);
  assert.deepEqual(await proof('request',requestId),a);assert.deepEqual(await preview('next','继续记录'),next);
  await h.api('send',next.command);
  const exact=await preview('exact',null,reply.id);assert.deepEqual(exact.p.messages,request.context);assert.equal(exact.p.requestHash,request.contextHash);
  const regen=await h.api('send',exact.command),candidate=await proof('request',regen.requestId);
  assert.equal(candidate.kind,'regenerate');assert.notEqual(candidate.requestId,requestId);assert.equal(candidate.rawText,raw);
  const newRow=JSON.parse(await shelf()).data.families[familyId],other=newRow.f.requests[regen.requestId];
  assert.deepEqual(other.context,request.context);assert.equal(candidate.receiptHash,other.receipt.hash);
  assert.deepEqual(newRow.f.requests[requestId],request);assert.deepEqual(await receiptBytes(),originalBytes);
  const child=await h.write('fork',{...loc,point:reply.id,title:'值班副本',session:null,player:null});
  assert.deepEqual(await proof('message',reply.id,{branchId:child.branchId}),a);
  await assert.rejects(proof('request',requestId,{branchId:child.branchId}),/DISPLAY_REFERENCE/);
  assert.equal(digest(exact.p.messages),a.contextHash);
});

test('applying a local display rule redraws only generated content and keeps original markup inert',()=>{
  const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
  const dom=new JSDOM('<button id="display-apply"></button><input id="display-enabled" type="checkbox"><input id="display-paragraphs" type="checkbox"><input id="display-find"><input id="display-replacement"><output id="display-status"></output><ol id="history"></ol>',{url:'http://127.0.0.1/'});
  try{
    const doc=dom.window.document,get=id=>doc.getElementById(id),loc={familyId:'fam',branchId:'root',targetId:'a'};
    const original='<em>蓝牌</em>\\n\\n渡口',list=Object.freeze([false,true].map((canRegenerate,i)=>Object.freeze({id:String(i),content:original,speakerId:i?'a':'player',canRegenerate})));
    const ui=createDisplayPanel(doc,()=>assert.fail('display rule must not read or send a request'));
    ui.show(loc,list);get('display-enabled').checked=true;get('display-find').value='蓝牌';get('display-replacement').value='青牌';
    get('display-apply').click();
    assert.equal(get('history-0').textContent,original);assert.equal(get('history-1').querySelector('.response-text').textContent,'<em>青牌</em>\n\n渡口');
    assert.equal(get('history-1').querySelector('pre').textContent,original);assert.equal(doc.querySelector('em'),null);
    ui.show({...loc,targetId:'b'},list);assert.equal(get('history-1').querySelector('.response-text').textContent,original);
    createDisplayPanel(doc,()=>assert.fail()).show(loc,list);assert.equal(get('display-enabled').checked,true);
    assert.equal(get('history-1').querySelector('pre').textContent,original);assert.equal(list[1].content,original);
  }finally{dom.window.close();}
});
