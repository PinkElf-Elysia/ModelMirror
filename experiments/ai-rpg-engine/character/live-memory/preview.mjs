// Offline preview only. Every route, model choice and text fragment is synthetic.
import {resolve} from 'node:path';
import {mkdir} from 'node:fs/promises';
import {parameterMock,token} from '../revisions/parameter-mock.mjs';
import {startLiveWorkspace} from './workspace.mjs';
import {blankDocument,sha256} from '../../authoring/contracts.mjs';
import {characterDocument} from '../document.mjs';
import {digest} from '../revisions/contracts.mjs';

const directory=resolve(process.argv[2]),port=Number(process.argv[3]??43960);
await mkdir(directory,{recursive:true});
const streams=new Map(),cleanup=[];
const chunks=['先把集合地点记下来。','我们可以在桥边等候，','等大家确认后再出发。'];
const mock=await parameterMock({after:f=>cleanup.push(f)},{port:port+6,
  text(p){
    if(p.purpose==='story')return chunks.join('');
    const m=JSON.parse(p.messages[1].content).at(-1);
    return JSON.stringify({content:'最近一次发言提到：'+m.content,refs:[{id:m.id,revisionId:m.revisionId}]});
  },
  async onTask(p,f){
    if(!p.messageContract?.includes('live-'))return;
    const stream={text:'',stop:false,status:'active'};
    streams.set(p.requestId,stream);
    for(const part of chunks){
      if(stream.stop)break;stream.text+=part;
      await new Promise(r=>setTimeout(r,750));
    }
    if(stream.stop){
      const v=f.results.get(p.sessionId+'/'+p.requestId);
      v.raw=stream.text;v.sse='fixture:'+v.raw;v.status='unknown';
      const {sha256:old,...r}=v.receipt;
      Object.assign(r,{status:'unknown',error:'rpg_authoring_host_stopped',finishReason:null,rawHash:sha256(v.raw),sseHash:sha256(v.sse)});
      v.receipt={...r,sha256:digest(r)};
    }
    stream.status=stream.stop?'unknown':'completed';
  },
  live(url,method){
    const fields=url.pathname.split('/'),r=streams.get(fields.at(method==='POST'?-2:-1));
    if(method==='POST'&&r?.status==='active')r.stop=true;
    const cursor=Number(url.searchParams.get('cursor')),text=[...(r?.text??'')];
    return {format:'modelmirror.rpg.live-progress/1',cursor,next:text.length,text:text.slice(cursor).join(''),
      status:r?.status??'unknown',stopRequested:r?.stop??false};
  }
});
const choice=await mock.control.selectParameters(mock.selection('story',{maxOutputTokens:{mode:'explicit',value:256}}));
const model={id:choice.entry.modelId,label:'中性离线夹具',choice,contextTokens:choice.entry.contextLength,outputTokens:256,
  price:{revision:'fixture',overheadTokens:256,inputMicroUsdPerMillion:1000000,outputMicroUsdPerMillion:1000000}};
const app=await startLiveWorkspace({directory,port,upstream:mock.origin,serviceToken:token,models:[model],
  executionLabel:'离线夹具',authorization:{id:'local-fixture',calls:32,microUsd:500000,initial:{calls:0,microUsd:0}},
  proposalLimit:{calls:8,microUsd:60000}});
const refs=[];
for(const [index,name] of ['渡口记录员','巡桥人'].entries()){
  const doc=characterDocument(blankDocument());Object.assign(doc.character.card.data,{name,description:'原创中性测试角色',first_mes:'我们在桥边集合。'});
  doc.metadata.name=name;const work=await app.studio.works.create({operationId:'work-'+index,expectedRevision:null,document:doc});
  const v=await app.studio.works.publish(work.workId,{operationId:'seal-'+index,expectedRevision:0});
  refs.push({workId:work.workId,versionId:v.result.versionId});
}
const player={id:null,name:'测试玩家'};
const group=await app.workspace.group.create({operationId:'group',expectedRevision:null,payload:{player,scene:'石桥边',members:refs.map(reference=>({reference,modelId:model.id})),
  opening:{memberIndex:0,kind:'ordinary',index:0},budget:{calls:24,microUsd:400000}}});
const single=await app.workspace.single.create({operationId:'single',reference:refs[0],openingIndex:0,player});
for(const [kind,item] of [['group',group],['single',single]]){
  const a=await app.longContext.store.importSource({operationId:'context-'+kind,expectedRevision:null,reference:{kind,sourceId:item.id}});
  await app.memory.store.importSource({operationId:'memory-'+kind,expectedRevision:null,reference:{kind:'context',familyId:a.id,branchId:a.branchId}});
}
console.log(JSON.stringify({preview:app.livePage.origin+'/live',memory:app.memory.page.origin+'/revisions',mode:'offline-fixture'}));
for(const sig of ['SIGTERM','SIGINT'])process.once(sig,async()=>{await app.close();for(const f of cleanup)await f();process.exit(0);});
