import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {loadAdapter} from './adapter.mjs';
import {assembleContext} from './context.mjs';
import {exampleState,OPENING} from './example.mjs';
import {hash} from './definition.mjs';
const directory=fileURLToPath(new URL('../.rpg03-work/local-state-real-20261113/',import.meta.url));
const adapter=await loadAdapter();
const inputs=[
 '服用两份养息丹，然后帮周禾扶好竹架。只推进这段日常，不增加交易、奖励或突破。',
 '停在原处查看药囊，回想刚才帮忙的经过。只描写当前所见，不消耗资源、不推进修炼、不新增互动或事件。'
];
const initial=exampleState();
const record={source:{history:[]},config:{profiles:[],mods:[]},turns:[],events:[],state:initial,stateRevision:0};
const context=await assembleContext(record,inputs[0]);
const testConstraint='【本次独立实测场景】人物均为成年人；只进行非性化的药圃日常，不生成成人向内容或选项。开局：'+OPENING;
context.messages[0].content+='\n\n'+testConstraint;
context.hash=hash(context.messages);
const inputAdmissionUnits=context.messages.reduce((n,m)=>n+Buffer.byteLength(m.content,'utf8')+32,256);
if(inputAdmissionUnits>600000)throw Error('INPUT_ENVELOPE_EXCEEDED');
const plan={format:'modelmirror.rpg.local-state-real-test/1',model:'google/gemini-3.8-flash',provider:'google-ai-studio',parameters:{temperature:0.7,top_p:0.8,max_tokens:32768},limits:{maxCalls:2,perCallUSD:0.60,totalUSD:1.20,automaticRetries:0,inputAdmissionUnits:600000},pricing:{inputPerMillion:0.75,outputPerMillion:3.75,currency:'USD',source:'https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints'},binding:adapter.binding,inputs,testConstraint,initialHash:hash(initial),r1MessagesHash:context.hash,r2Gate:'R1 complete, delta accepted and committed; freeze R2 using actual R1 body and state, never synthetic continuation',scope:'isolated real-call acceptance; no deployment, shared data migration, commit, push or PR',authorization:'User authorized at most two Gemini calls, USD 0.60 each and USD 1.20 total; no automatic retry.'};
await mkdir(directory,{recursive:true});
async function freeze(name,value){const data=JSON.stringify(value,null,2)+'\n';try{await writeFile(join(directory,name),data,{flag:'wx'});}catch(e){if(e.code!=='EEXIST'||await readFile(join(directory,name),'utf8')!==data)throw e;}}
await freeze('plan.json',plan);await freeze('initial.json',initial);await freeze('R1-context.json',context);
console.log(JSON.stringify({prepared:true,planHash:hash(plan),r1MessagesHash:context.hash,inputAdmissionUnits,admissionIsNotTokenCount:true,reserveUSD:600000*.00000075+32768*.00000375,callsDispatched:0,directory}));
