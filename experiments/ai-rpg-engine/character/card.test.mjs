import test from 'node:test';import assert from 'node:assert/strict';
import {blankCard,validateCard,downgrade,compatibility} from './card.mjs';
import {inspectImport,exportCard} from './exchange.mjs';
import {placeholder,chunk,pngChunks,decode64} from './png.mjs';
import {assembleCharacter,greeting} from './context.mjs';
const card=()=>{const c=blankCard();Object.assign(c.data,{name:'林舟（测试夹具）',description:'中文\n第二行 {{char}}',first_mes:'你好，{{user}}。',alternate_greetings:['候选开场'],extensions:{unknown:{nested:['中文',1,null]}}});return c;};
test('V2 JSON and PNG preserve unknown fields and Unicode',()=>{for(const format of ['json','png']){const c=card();c.extra={retained:true};const out=exportCard(c,{format});assert.deepEqual(inspectImport(out.bytes).card,c);}});
test('plain picture, corrupt CRC, types and conflicting metadata reject',()=>{
 assert.throws(()=>inspectImport(placeholder()),{code:'CHARACTER_METADATA_MISSING'});const p=exportCard(card(),{format:'png'}).bytes;p[20]^=1;assert.throws(()=>inspectImport(p),{code:'CHARACTER_PNG_CRC'});
 const c=card();c.data.alternate_greetings=[1];assert.throws(()=>validateCard(c),{code:'CHARACTER_FIELD_TYPE'});
 const b=placeholder();const t=s=>chunk('tEXt',Buffer.from('chara\0'+Buffer.from(JSON.stringify(s)).toString('base64')));const c2=card();c2.data.name='另一人';
 assert.throws(()=>inspectImport(Buffer.concat([b.subarray(0,-12),t(card()),t(c2),b.subarray(-12)])),{code:'CHARACTER_METADATA_CONFLICT'});
});
test('V3 priority, corrupt V3 requires explicit V2 selection, downgrade warns',()=>{
 const c=card(),v={...structuredClone(c),spec:'chara_card_v3',spec_version:'3.0'};v.data.group_only_greetings=[];v.data.nickname='小舟';v.data.assets=[{type:'icon',uri:'https://invalid.test/icon',name:'x',ext:'png'}];
 const p=exportCard(c,{format:'png'}).bytes,t=chunk('tEXt',Buffer.from('ccv3\0'+Buffer.from(JSON.stringify(v)).toString('base64')));
 assert.deepEqual(inspectImport(Buffer.concat([p.subarray(0,-12),t,p.subarray(-12)])).card,v);
 const bad=Buffer.concat([p.subarray(0,-12),chunk('tEXt',Buffer.from('ccv3\0broken')),p.subarray(-12)]);
 assert.throws(()=>inspectImport(bad),{code:'CHARACTER_METADATA_SELECTION_REQUIRED'});assert.deepEqual(inspectImport(bad,{selection:'chara'}).card,c);
 assert.equal(exportCard(v,{version:'v2'}).confirmationRequired,true);assert.ok(downgrade(v).losses.includes('/data/assets'));
});
test('assembly separates metadata, examples, frozen greeting and post-history',()=>{
 const c=card();c.data.creator_notes='NEVER_PROMPT';c.data.mes_example='<START>\n{{char}}: 示例';c.data.system_prompt='{{original}}\n{{CHAR}}';c.data.post_history_instructions='尾部 {{user}} {{original}}';
 const a=assembleCharacter({card:c,player:'青禾',opening:greeting(c,0,'青禾'),input:'你好'});
 assert.equal(a.messages[1].role,'assistant');assert.equal(a.messages[1].content,'你好，青禾。');assert.equal(a.messages.at(-1).role,'system');assert.ok(!JSON.stringify(a.messages).includes('NEVER_PROMPT'));assert.equal(a.messages.filter(x=>x.content.includes('<START>')).length,1);
});
test('book selection, bounded recursion, priority and unsupported behavior',()=>{
 const c=card(),entry=(keys,content,extra={})=>({keys,content,enabled:true,extensions:{},insertion_order:0,...extra});
 c.data.character_book={extensions:{preserve:true},recursive_scanning:true,entries:[entry(['灯塔'],'灯塔引向港口'),entry(['港口'],'地图', {position:'before_char'}),entry(['秘密'],'不要泄露'),entry(['灯塔'],'@@depth 0\nunsupported'),entry(['灯塔'],'selective',{selective:true,secondary_keys:['钥匙']})]};
 const a=assembleCharacter({card:c,opening:'开场',input:'灯塔'});assert.deepEqual(a.trace.book.map(x=>x.status),['triggered','triggered','not-triggered','unsupported-behavior','not-triggered']);assert.ok(!a.messages[0].content.includes('不要泄露'));
});

test('size, base64, encoding, duplicate keys and JSON complexity fail explicitly',()=>{
 const large=Buffer.alloc(8*1024*1024,32);assert.equal(decode64(large.toString('base64')).length,large.length);
 assert.throws(()=>decode64('AB=='),{code:'CHARACTER_BASE64_INVALID'});
 assert.throws(()=>inspectImport(Buffer.alloc(16*1024*1024+1,32)),{code:'CHARACTER_FILE_SIZE'});
 assert.throws(()=>pngChunks(Buffer.alloc(32*1024*1024+1)),{code:'CHARACTER_FILE_SIZE'});
 assert.throws(()=>inspectImport(Buffer.from([0xff])),{code:'CHARACTER_UTF8_INVALID'});
 assert.throws(()=>inspectImport(Buffer.from('{"spec":1,"spec":2}')));
 const c=card();let v=c.data.extensions;for(let i=0;i<65;i++)v=v.deep={};assert.throws(()=>inspectImport(Buffer.from(JSON.stringify(c))),{code:'AUTHORING_JSON_COMPLEX'});
});

test('book ordering accepts signed numbers, explicit budgets and reports alternate macros',()=>{
 const c=card();c.data.alternate_greetings=['{{unsupported}}'];c.data.character_book={extensions:{},token_budget:2000,entries:[{keys:['灯塔'],content:'光'.repeat(1200),enabled:true,insertion_order:-1,priority:-2,extensions:{},case_sensitive:true}]};
 assert.ok(compatibility(c).warnings.some(w=>w.path==='/data/alternate_greetings/0'));
 const result=assembleCharacter({card:c,opening:'你好',input:'灯塔'});assert.equal(result.trace.book[0].status,'triggered');assert.ok(result.messages[0].content.includes('光'.repeat(1200)));
 assert.throws(()=>inspectImport(Buffer.from('PK archive')),{code:'CHARACTER_CHARX_UNSUPPORTED'});
 c.data.mes_example='例'.repeat(100);const without=structuredClone(c);without.data.mes_example='';delete without.data.character_book;delete c.data.character_book;
 const minimal=assembleCharacter({card:without,opening:'你好',input:'继续'}).trace.estimatedTokens;
 const small=assembleCharacter({card:c,opening:'你好',input:'继续',maxTokens:minimal+12});assert.equal(small.trace.examples,'budget-excluded');assert.ok(small.trace.estimatedTokens<=minimal+12);
});
