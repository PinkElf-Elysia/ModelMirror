import test from 'node:test';
import assert from 'node:assert/strict';
import {blankCard} from '../card.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {parseExamples,serializeExamples} from './examples.mjs';
import {source} from '../revisions/test-support.mjs';
import {apply,digest} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {assemble} from '../book-library/assembly.mjs';
import {defaults} from '../long-context/planner.mjs';
import {sources} from '../live-memory/protocol.mjs';
import {upgradeCard} from './metadata.mjs';
const raw='<START>\r\n{{user}}: 同名\r\n第二行\r\n\r\n{{user}}:   仍是玩家\r\n<START>\r\n{{char}}:示例暗号\r\n';
test('raw examples preserve CRLF, blank lines, spacing, repeated speakers and exact grouping',()=>{
 const p=parseExamples(raw);assert.equal(p.groups.length,2);assert.equal(serializeExamples(p.groups,p.eol),raw);
 p.groups[0].reverse();p.groups[1][0].body+='添一行';const changed=serializeExamples(p.groups,p.eol);
 const card=blankCard();upgradeCard(card);card.data.mes_example=changed;
 for(const format of ['json','png']){const result=inspectImport(exportCard(card,{format}).bytes);assert.deepEqual(parseExamples(result.card.data.mes_example),p);}
 for(const text of ['路人：你好','<START>\n守桥人: 地点','<START>\n{{user}}: 正文里有 <START>','<START>\r\n{{user}}: a\nb','<START>\n<START>'])assert.ok(parseExamples(text).line,text);
 const g=[[{role:'{{char}}',space:' ',body:'引用\n{{user}}: 假标记'}]];assert.throws(()=>serializeExamples(g,'\n'),/标记/);
 assert.deepEqual(parseExamples('').groups,[]);
});
test('example-only keyword stays outside book activation, visible transcript, proposal sources and receipts',()=>{
 const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card);card.data.mes_example=raw;
 card.data.character_book={extensions:{},entries:[{keys:['/示例暗号/u'],enabled:true,use_regex:true,content:'不应激活',extensions:{},insertion_order:0}]};
 s.current.members[1].binding={reference:{versionId:'sealed'}};delete s.sourceHash;s.sourceHash=digest(s);
 const f=apply(null,{action:'create',operationId:'seed',expectedRevision:null,payload:{source:s}}),p={branchId:f.rootId,input:'你好',kind:'send',targetId:'actor'};
 const run=(model)=>assemble(f,'example',p,model,defaults(),[],{},true);
 const withExample=run(fixtureModel());assert.equal(withExample.books.entries[0].status,'not-triggered');
 assert.match(withExample.request.context[0].content,/对话示例[\s\S]*示例暗号/);assert.doesNotMatch(JSON.stringify(withExample.request.context.slice(1)),/示例暗号|不应激活/);
 assert.doesNotMatch(JSON.stringify(sources(f.branches[f.rootId],'actor')),/示例暗号/);
 assert.deepEqual(f.requests,{});assert.deepEqual(f.branches[f.rootId].messages,s.messages);
 card.data.mes_example='示例暗号'.repeat(10000);f.source.cards.actor.document.character.card.data.mes_example=card.data.mes_example;
 const clipped=run({...fixtureModel(),contextTokens:5000});assert.equal(clipped.plan.trace.find(t=>t.kind==='examples').status,'omitted');assert.doesNotMatch(JSON.stringify(clipped.request.context),/示例暗号/);
});