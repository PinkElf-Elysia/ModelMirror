import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startAuthoringServer} from './server.mjs';
import {OfflineFixtureProvider} from './offline-session.mjs';
import {OfflineModelControl} from './model-control.mjs';
import {blankDocument,sha256} from './contracts.mjs';
import {createPlaybackFrames} from './playback-ui.mjs';
import {renderPanelTemplateBounded} from './variable-template.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/variable-http-tests/',import.meta.url));await mkdir(root,{recursive:true});
const config={enabled:true,skeleton:'{\r\n  "地点": "码头", "体力":80\r\n}',template:'<style>.vp{color:teal}</style><div class="vp">{{地点}} {{体力}}</div><button data-panel-copy="{{地点}}">复制</button><script>window.steal=true</script>',instructions:'只更新已发生事件。'};
const raw='抵达灯塔。<variable_panel>{"地点":"灯塔","体力":70}</variable_panel>';
const catalog={catalogId:'panel-http',models:[{selectionId:'story',selectionRevision:sha256('story'),modelId:'fixture/story',name:'离线剧情',available:true,contextLength:128000,maxOutputTokens:4096,parameterPolicy:{kind:'fixed',fields:{maxOutputTokens:{minimum:4096,maximum:4096,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}]};
async function setup(t){
 const dir=await mkdtemp(join(root,'case-')),provider=await OfflineFixtureProvider.open(join(dir,'provider'),{replies:{one:{status:'completed',finishReason:'stop',text:raw}}}),control=new OfflineModelControl(catalog);
 const server=await startAuthoringServer({directory:join(dir,'works'),creatorExperience:true,modelSettings:{directory:join(dir,'sessions'),provider,control},historySettings:{directory:join(dir,'plugins')},jointSettings:{directory:join(dir,'joint'),allowance:{id:'panel-budget',limit:2}},playbackSettings:{directory:join(dir,'display')}});
 t.after(async()=>{await server.close();await provider.close();});
 const req=async(path,body,headers={})=>{const r=await fetch(server.origin+'/rpg-app/api/authoring'+path,body===undefined?{headers}:{method:'POST',headers:{Origin:server.origin,'content-type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
 const ok=async(path,body)=>{const r=await req(path,body);assert.equal(r.status,200,JSON.stringify(r));return r.value;};
 const doc=blankDocument();doc.metadata.name='独立变量样例';doc.metadata.audience='male';doc.sources.system='主持';doc.settings.variablePanel=structuredClone(config);
 const w=await ok('/works',{operationId:'work',expectedRevision:null,document:doc});
 const frame=async p=>{const r=await fetch(server.origin+p.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}});assert.equal(r.status,200);return {html:await r.text(),csp:r.headers.get('content-security-policy')};};
 return {server,provider,control,req,ok,doc,w,frame};
}
test('preview is bounded, read-only, revision checked and scripts removed',async t=>{
 const f=await setup(t),body={workId:f.w.workId,expectedRevision:0,config};
 const p=await f.ok('/variable-panel/preview',body),r=await f.frame(p);
 assert.match(r.html,/码头 80/);assert.doesNotMatch(r.html,/window.steal/);assert.match(r.html,/data-panel-copy="码头"/);assert.match(r.csp,/connect-src 'none'/);assert.equal(p.manifest.authorScriptsExecuted,false);assert.equal(f.provider.dispatchCount,0);assert.equal((await f.ok('/works/'+f.w.workId)).revision,0);
 for(const [change,code] of [[{expectedRevision:9},'AUTHORING_REVISION_CONFLICT'],[{config:{...config,skeleton:'{bad'}},'AUTHORING_JSON_SYNTAX'],[{config:{...config,template:'{{> private}}'}},'PANEL_TEMPLATE_UNSUPPORTED']]){const bad=await f.req('/variable-panel/preview',{...body,...change});assert.equal(bad.value.error.code,code);}
 assert.equal((await f.req('/variable-panel/preview',body,{Origin:'https://outside.invalid'})).status,403);
 await f.ok('/presentations/release',{token:p.token});
});
test('published playback reads frozen state, display strips only the valid envelope, raw and draft remain intact',async t=>{
 const f=await setup(t),v=await f.ok('/works/'+f.w.workId+'/publish',{operationId:'publish',expectedRevision:0});
 const made=await f.ok('/model-settings/sessions',{operationId:'session',expectedRevision:null,reference:{workId:f.w.workId,versionId:v.result.versionId},openingText:'出发',presetId:null}),id=made.session.id;
 const view=()=>f.ok('/joint/sessions/'+id);let s=(await view()).session;
 await f.ok('/model-settings/configure',{sessionId:id,operationId:'model',expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose:'story',choice:{catalogRevision:f.control.catalog().revision,selectionId:'story',selectionRevision:sha256('story'),parameters:{maxOutputTokens:4096}}});
 const initial=await f.ok('/joint/playback/panel',{sessionId:id,turnNumber:0});assert.equal(initial.snapshot.revision,0);
 const current=await view();s=current.session;await f.ok('/joint/send',{sessionId:id,operationId:'one',expectedRevision:s.revision,expectedModelRevision:s.models.revision,expectedContextPolicyRevision:current.effectivePolicy.revision,expectedSummaryRevision:s.summary.snapshot.revision,expectedMemoryRevision:s.memory.snapshot.revision,kind:'send',input:'出发'});await f.server.sessionHost.waitForIdle(id);
 const p=await f.ok('/joint/playback/panel',{sessionId:id,turnNumber:1}),r=await f.frame(p);assert.equal(p.snapshot.data.地点,'灯塔');assert.match(r.html,/灯塔 70/);
 const story=await f.ok('/joint/playback/prepare',{sessionId:id,turnNumber:1,role:'assistant',device:'desktop'}),rendered=await f.frame(story);assert.doesNotMatch(rendered.html,/<variable_panel>/);assert.equal(story.manifest.binding.rawHash,sha256(raw));assert.equal((await view()).session.turns[0].assistant,raw);
 const w=await f.ok('/works/'+f.w.workId),draft=structuredClone(w.draft);draft.settings.variablePanel.template='<p>未来版本</p>';await f.ok('/works/'+f.w.workId+'/save',{operationId:'edit',expectedRevision:w.revision,document:draft});
 const replay=await f.ok('/joint/playback/panel',{sessionId:id,turnNumber:1});assert.deepEqual(replay.snapshot,p.snapshot);assert.equal(replay.manifest.renderHash,p.manifest.renderHash);assert.equal(f.provider.dispatchCount,1);
 for(const prepared of [initial,p,story,replay])await f.ok('/presentations/release',{token:prepared.token});
});
test('panel rendering error cannot hide either story frame or trigger a model call',async()=>{
 const rows=[],changed=[];const api=async(path,body)=>{rows.push({path,body});if(path.endsWith('/panel'))throw {code:'PANEL_TEMPLATE_TIMEOUT'};return {token:body.role};};
 const frames=createPlaybackFrames({api,changed:e=>changed.push(e)});await frames.load('turn',{sessionId:'s',turnNumber:1},{panel:true});assert.deepEqual(frames.frames.map(f=>f.token),['user','assistant']);assert.equal(changed.at(-1).error,null);assert.equal(changed.at(-1).panelError.code,'PANEL_TEMPLATE_TIMEOUT');frames.clear();assert.ok(rows.every(r=>!r.path.endsWith('/send')));
});
test('template timeout terminates and later rendering still works',async()=>{
 await assert.rejects(renderPanelTemplateBounded('{{#each values}}{{this}}{{/each}}',{values:[1,2,3]},{timeoutMs:1}),{code:'PANEL_TEMPLATE_TIMEOUT'});
 assert.equal(await renderPanelTemplateBounded('{{地点}}',{地点:'码头'}),'码头');
});
