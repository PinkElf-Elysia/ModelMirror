import assert from 'node:assert/strict';
import test from 'node:test';
import {compileKeys,match,meter,limits,StepLimit} from './regex.mjs';
import {withMeter,selectV3} from './selection.mjs';
import {blankCard} from '../card.mjs';
import {startPreview} from '../book-library/preview.mjs';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';import {tmpdir} from 'node:os';
const entry=(keys,content,extra={})=>({keys,content,use_regex:true,enabled:true,insertion_order:0,extensions:{},...extra});
const run=(pattern,s)=>match(compileKeys([pattern]).programs[0],[...s],meter());
test('bounded subset, Unicode codepoints, ASCII i, LF m, anchors, escaped punctuation and repetitions',()=>{
 for(const [p,yes,no] of [['/^(渡口|码头)[0-9]{1,2}$/m','早\n码头12\n晚','码头123'],['/^A[^b-d]+z?$/iu','a🌊Z','acz'],['/^(ab)*c$/','ababc','ababa'],['/^甲.{2}乙$/u','甲🌊A乙','甲\nA乙'],['/a\\/b/','xxa/b','ab'],['/^Ä$/i','Ä','ä'],['/^hi$/m','x\nhi\ny','x hi y']]){
  assert.ok(run(p,yes),p);assert.ok(!run(p,no),p);
 }
 for(const p of ['/a*/','/^$/','/()|a/'])assert.equal(compileKeys([p]).status,'regex-empty');
 for(const p of ['/(?=a)a/','/(a)\\1/','/a+?/','/a/g','hello','/\\p{Han}/u'])assert.equal(compileKeys([p]).status,'regex-unsupported');
 for(const p of ['/a/ii','/[z-a]/','/(/','/a{9,2}/'])assert.equal(compileKeys(['/ok/',p]).key,1);
});
test('hard limits cover compile, text, cumulative work and exact boundary',()=>{
 assert.ok(compileKeys(['/a/','/b/','/c/','/d/','/e/','/f/','/g/','/h/']).programs);
 assert.equal(compileKeys(Array(9).fill('/a/')).status,'regex-limit');
 assert.ok(compileKeys(['/'+'a'.repeat(256)+'/']).programs);assert.equal(compileKeys(['/'+'a'.repeat(257)+'/']).status,'regex-limit');
 assert.ok(compileKeys(['/((((a)))){32}/']).programs);for(const p of ['/(((((a)))))/','/a{33}/','/(a{32}){32}/'])assert.equal(compileKeys([p]).status,'regex-limit');
 const c=blankCard();c.data.character_book={extensions:{},entries:[entry(['/a/'],'MATCH')]};
 const select=(s,clock)=>selectV3(c,[],s,{},clock);
 assert.equal(select('a'.repeat(limits.scan),meter()).trace[0].status,'triggered');assert.equal(select('a'.repeat(limits.scan+1),meter()).trace[0].status,'regex-scan-limit');
 const pattern=compileKeys(['/((a|aa)+)+z/']).programs[0],clock=meter();clock.remaining=100;
 assert.throws(()=>match(pattern,[...'a'.repeat(1000)],clock),StepLimit);assert.equal(clock.remaining,0);
 c.data.character_book.entries.push(entry(Array(8).fill('/((a|aa)+)+z/'),'slow'),entry(['a'],'普通',{use_regex:false}));
 const result=withMeter(clock=>select('a'.repeat(16000),clock));assert.deepEqual(result.after,['普通']);assert.deepEqual(result.trace.map(r=>r.status),['regex-request-limit','regex-request-limit','triggered']);
});
test('all keys validated, constant/secondary ignored, recursive matches stay in the same book',()=>{
 const c=blankCard();c.data.character_book={extensions:{},recursive_scanning:true,entries:[entry(['/hello/i'],'桥'),entry(['/桥/'],'灯',{selective:true,secondary_keys:['永不出现']}),entry(['/hello/','/[/'],'错'),entry(['/zz/'],'常驻',{constant:true}),entry(['/hello/'],'@@unsupported'),entry(['/hello/'],'停用',{enabled:false})]};
 const result=withMeter(clock=>selectV3(c,[],'Hello',{},clock));assert.deepEqual(result.after,['桥','灯']);assert.deepEqual(result.trace.map(r=>r.status),['triggered','triggered','regex-invalid','not-triggered','unsupported-behavior','disabled']);assert.equal(result.trace[2].key,1);
});
test('opt-in branch isolates readers, old strategy unchanged, previews match stored fixture request; restart and exact generation',async t=>{
 const root=process.env.CHARACTER_R5A_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'rules-'));let host=await startPreview(dir);t.after(()=>host.close());
 const api=(a,p)=>host.api(a,p),write=(a,p)=>host.write(a,p);
 const cat=await api('catalog'),parent={familyId:cat.families[0].id,branchId:cat.families[0].branches[0].id},actors=(await api('view',{...parent,targetId:null})).members;
 const b=await write('save',{bookId:null,title:'潮汐私册',book:{entries:[entry(['/^潮[0-9]{2}$/m'],'私册正文')],extensions:{}}});
 const ref=await write('seal',b),fork={...parent,title:'新规',point:null,session:null,player:[{ref,readers:[actors[0].id]}]};
 const old=await write('fork',fork),v=await write('fork',{...fork,regexPolicy:'v3-bounded/1'});
 const see=(branch,actor,operationId,input='潮12',messageId=null)=>{const intent={branchId:branch.branchId,targetId:actor.id,input,messageId};return api('preview',{familyId:v.familyId,intent,operationId}).then(result=>({result,intent}));};
 assert.doesNotMatch(JSON.stringify((await see(old,actors[0],'old')).result.messages),/私册正文/);
 assert.doesNotMatch(JSON.stringify((await see(v,actors[1],'other')).result),/私册正文|潮汐私册/);
 const {intent,result}=await see(v,actors[0],'dispatch');assert.match(result.messages[0].content,/私册正文/);
 const reply=await api('send',{operationId:'dispatch',expectedRevision:result.revision,payload:{familyId:v.familyId,intent,previewHash:result.previewHash}});
 const shelf=JSON.parse(await readFile(join(dir,'studio','character','independent-books','library','records','shelf.json'),'utf8')).data;
 assert.deepEqual(shelf.families[v.familyId].f.requests[reply.requestId].context,result.messages);
 await host.close();host=await startPreview(dir);
 const messages=(await api('view',{...v,targetId:actors[0].id})).messages;assert.deepEqual((await see(v,actors[0],'regen',null,messages.at(-1).id)).result.messages,result.messages);
});