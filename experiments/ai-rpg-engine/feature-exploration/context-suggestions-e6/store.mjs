import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,need,keys} from '../../agent-core/contracts.mjs';
import {suggest} from './suggestions.mjs';
import {stamp} from './source.mjs';
export const giftText=n=>'我想赠送周禾'+n+'份养息丹。';
export class Choices{
 static async open(directory,host){return new Choices(await SessionRecords.open(directory,{scope:'e6-choices',recoverDeadOwner:true}),host);}
 constructor(records,host){this.records=records;this.host=host;this.queue=Promise.resolve();}
 async view(){const row=await this.records.get('choices');const frame=await this.host.reader.read();
 return {frame,revision:row?.data.revision??0,result:row?.data.result??null,saved:row?.data.saved??[],
 stale:!!row?.data.result&&row.data.result.source!==stamp(frame),mode:'offline-fixture',realCalls:0};}
 command(c){const p=this.queue.then(()=>this.run(c));this.queue=p.catch(()=>{});return p;}
 async run(c){
 keys(c,['kind','revision','goal','answer','optionId','text','quantity']);
 const row=await this.records.get('choices'),data=row?clone(row.data):{revision:0,result:null,saved:[]};
 need(c.revision===data.revision,'E6_CONFLICT');const frame=await this.host.reader.read();
 if(c.kind==='suggest')data.result=suggest(frame,c.goal,c.answer??'');
 else if(c.kind==='dismiss')data.result=null;
 else if(c.kind==='save'){
 need(typeof c.text==='string'&&c.text.trim()&&c.text.length<=2000,'E6_TEXT');need(data.saved.length<30,'E6_SAVED_LIMIT');
 const option=c.optionId?data.result?.options.find(x=>x.id===c.optionId):null;
 if(c.optionId){need(option,'E6_OPTION');need(data.result.source===stamp(frame)&&frame.ready,'E6_STALE');}
 let plan=null;
 const id='choice-'+(data.revision+1);
 if(option?.kind==='gift'&&c.quantity!==undefined){
 need(Number.isSafeInteger(c.quantity)&&c.quantity>0,'E6_QUANTITY');
 need(c.text===giftText(c.quantity),'E6_EDITED_TEXT');
 plan=await this.host.selectedPlan({id,kind:'gift',origin:'player-selection',targetId:option.targetId,itemId:option.itemId,quantity:c.quantity,text:c.text},stamp(frame));
 }
 // Edited/free text is saved as literal intent, never silently parsed into an action.
 need(stamp(await this.host.reader.read())===stamp(frame),'E6_STALE');
 data.saved.push({id,text:c.text,origin:option?'player-selection':'player-free-input',
 modelSuggestion:option?{mode:'offline-fixture',id:option.id,text:option.text}:null,
 goal:data.result?.goal??null,source:clone(frame.source),plan,executionStatus:'not_executed'});
 }else need(false,'E6_COMMAND');
 data.revision++;await this.records.put('choices',row?.hash??null,data);return this.view();
 }
 close(){return this.records.close();}
}
