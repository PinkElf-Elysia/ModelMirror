import test from 'node:test';
import assert from 'node:assert/strict';
import {blankDocument,validateDocument,publicationIssues,canonical,sha256} from './contracts.mjs';
import {panelDefinition,initialPanelSnapshot,advancePanel,parsePanelData,panelIssues,panelVisibleReply} from './variable-panel.mjs';
import {renderPanelTemplateBounded} from './variable-template.mjs';
const config={enabled:true,skeleton:JSON.stringify({地点:'码头',体力:80,任务:{完成:false},背包:[]}),template:'<h2>{{地点}}</h2>',instructions:'只记录已经发生的变化。'};
const definition=panelDefinition(config),initial=initialPanelSnapshot(definition);
const raw=data=>'抵达灯塔。<variable_panel>'+JSON.stringify(data)+'</variable_panel>';
test('optional source config retains exact text; old documents stay unchanged',()=>{
 const doc=blankDocument(),before=canonical(doc);validateDocument(doc);assert.equal(canonical(doc),before);
 doc.settings.variablePanel={...config,skeleton:'{invalid'};validateDocument(doc);assert.ok(panelIssues(doc.settings.variablePanel).length);
 doc.settings.variablePanel.enabled=false;assert.deepEqual(panelIssues(doc.settings.variablePanel),[]);
});
test('full state applies once, no-change and display preserve raw text',()=>{
 const data={...initial.data,地点:'灯塔',体力:70,背包:['海图']},original=raw(data),result=advancePanel(definition,initial,original);
 assert.equal(result.status,'applied');assert.equal(result.snapshot.revision,1);assert.deepEqual(initial.data.背包,[]);
 assert.equal(advancePanel(definition,result.snapshot,original).status,'unchanged');assert.equal(panelVisibleReply(original,result),'抵达灯塔。');assert.equal(result.sourceHash,sha256(original));
});
for(const [name,source] of Object.entries({missing:'只有剧情',empty:'<variable_panel></variable_panel>',type:raw({...initial.data,体力:'无限'}),field:raw({地点:'灯塔'}),duplicate:'<variable_panel>{"a":1,"a":2}</variable_panel>',many:raw(initial.data)+raw(initial.data),prototype:'<variable_panel>{"__proto__":{}}</variable_panel>',capacity:raw({...initial.data,大文本:'x'.repeat(66000)})}))test('reject and retain previous state: '+name,()=>{
 const result=advancePanel(definition,initial,source);assert.notEqual(result.status,'applied');assert.deepEqual(result.snapshot,initial);assert.equal(panelVisibleReply(source,result),source);
});
test('bounded skeleton rejects nested and non-object data',()=>{assert.throws(()=>parsePanelData('[]'));assert.throws(()=>parsePanelData('{"a":'+ '['.repeat(20)+'0'+']'.repeat(20)+'}'));});
test('Handlebars nested Chinese paths, list, comparisons, escaping and copy attributes',async()=>{
 const html=await renderPanelTemplateBounded('<h2>{{角色.姓名}}</h2>{{#if (gt 体力 0)}}行走{{else}}休息{{/if}}{{#each 背包}}<p>{{@index}}:{{this}}</p>{{/each}}<button data-panel-copy="{{地点}}">复制</button>',{角色:{姓名:'<script>x</script>'},体力:2,地点:'灯塔',背包:['海图','绳索']});
 assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('1:绳索'));assert.ok(html.includes('data-panel-copy="灯塔"'));assert.ok(html.includes('行走'));
});
for(const template of ['{{#each}}','{{> secret}}','{{lookup this "constructor"}}','{{#unknown x}}a{{/unknown}}'])test('template fails closed: '+template,async()=>{await assert.rejects(renderPanelTemplateBounded(template,{}),e=>e.code.startsWith('PANEL_TEMPLATE_'));});

test('draft save, disabled source and full package import preserve exact panel configuration',async()=>{
 const {AuthoringStore}=await import('./store.mjs');const {mkdtemp,mkdir}=await import('node:fs/promises');const {fileURLToPath}=await import('node:url');const {join}=await import('node:path');
 const root=fileURLToPath(new URL('../.rpg04-work/variable-package-tests/',import.meta.url));await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'case-'));
 const first=await AuthoringStore.open(join(dir,'first')),second=await AuthoringStore.open(join(dir,'second'));
 try{const doc=blankDocument();doc.metadata.name='面板往返';doc.metadata.audience='male';doc.sources.system='原样系统';doc.settings.variablePanel={...config,skeleton:config.skeleton+'\r\n',template:config.template+'\r\n',instructions:'变量要求\r\n'};
 const work=await first.create({operationId:'create',expectedRevision:null,document:doc}),pub=await first.publish(work.workId,{operationId:'publish',expectedRevision:0});
 const payload=await first.exportPackage(work.workId,pub.result.versionId);const imported=await second.importPackage({operationId:'import',expectedRevision:null,workId:null,payload,expectedImportHash:sha256(canonical(payload))});assert.deepEqual((await second.read(imported.workId)).draft.settings.variablePanel,doc.settings.variablePanel);
 const draft=structuredClone(doc);draft.settings.variablePanel.skeleton='{unfinished';await first.save(work.workId,{operationId:'save',expectedRevision:1,document:draft});await assert.rejects(first.publish(work.workId,{operationId:'bad-publish',expectedRevision:2}));
 draft.settings.variablePanel.enabled=false;await first.save(work.workId,{operationId:'disable',expectedRevision:2,document:draft});const disabled=await first.publish(work.workId,{operationId:'publish-disabled',expectedRevision:3});assert.equal((await first.exportConfiguration(work.workId,disabled.result.versionId)).document.settings.variablePanel.skeleton,'{unfinished');
 }finally{await first.close();await second.close();}
});

test('initial arrays cannot publish a shape that rejects their own unchanged snapshot',()=>{
 for (const items of [[1,'two'],[{name:'a'},{other:'b'}]]) {
  const candidate={...config,skeleton:JSON.stringify({items})};
  assert.equal(panelIssues(candidate)[0]?.code,'PANEL_SKELETON_SHAPE');
  assert.throws(()=>panelDefinition(candidate),{code:'PANEL_SKELETON_SHAPE'});
 }
 const mixed={...config,skeleton:JSON.stringify({items:[null,'any',12],empty:[]})};
 assert.deepEqual(panelIssues(mixed),[]);
 const def=panelDefinition(mixed),state=initialPanelSnapshot(def);
 assert.equal(advancePanel(def,state,raw(state.data)).status,'unchanged');
});
