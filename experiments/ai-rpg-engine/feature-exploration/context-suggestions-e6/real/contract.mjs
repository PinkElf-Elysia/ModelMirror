import {need} from '../../../agent-core/contracts.mjs';
import {build as base} from '../suggestions.mjs';
export const INPUTS=['我想向周禾表达谢意，但尽量保留自己的丹药，有哪些办法？先给我选择，不要替我行动。','我想感谢一个人，但先不送丹药，也还没决定怎么表达。你能帮我想想吗？'];
export function build(frame,index){
 const r=base(frame,INPUTS[index]);r.mode='real-test';
 r.messages[0].content+='\n只输出一个JSON对象，不输出执行结果。格式严格为{"question":null,"options":[{"id":"唯一短ID","title":"标题","text":"玩家可选择并修改的意图","reason":"为何回应目标","evidence":["people:zhou"],"unknown":"重要未知","support":"仅意图，不支持执行","kind":"conversation","targetId":"zhou","itemId":null}]}。question为一个关键澄清问题或null；有关键对象未明确时只问问题、options为空，不能凭仅有一个登记人物就预选对象。options最多三个。kind仅conversation或gift；targetId为登记人物ID或null，itemId为登记物资ID或null；gift不预填数量。evidence只可引用people:ID或items:ID。无证据的信息写在unknown而非当作事实。选项彼此独立，不是执行步骤。';
 return r;
}
const keys=(o,list)=>need(o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join()===list.sort().join(),'E6_FIELDS');
export function parse(raw,frame){
 let s=raw.trim();const f=/^\x60\x60\x60json\s*\n([\s\S]*?)\n\x60\x60\x60$/i.exec(s);if(f)s=f[1];
 const v=JSON.parse(s);keys(v,['question','options']);
 need(v.question===null||(typeof v.question==='string'&&v.question.trim()&&v.question.length<=500),'E6_QUESTION');
 need(Array.isArray(v.options)&&v.options.length<=3&&(!v.question||v.options.length===0),'E6_OPTIONS');
 const refs=new Set([...frame.facts.people.map(x=>'people:'+x.id),...frame.facts.resources.map(x=>'items:'+x.id)]),ids=new Set();
 for(const o of v.options){
 keys(o,['id','title','text','reason','evidence','unknown','support','kind','targetId','itemId']);
 for(const k of ['id','title','text','reason','unknown','support'])need(typeof o[k]==='string'&&o[k].trim()&&o[k].length<=2000,'E6_TEXT');
 need(!ids.has(o.id)&&/^[a-zA-Z0-9_-]{1,64}$/.test(o.id),'E6_ID');ids.add(o.id);
 need(['conversation','gift'].includes(o.kind),'E6_KIND');
 need(o.targetId===null||frame.facts.people.some(x=>x.id===o.targetId),'E6_TARGET');
 need(o.itemId===null||frame.facts.resources.some(x=>x.id===o.itemId),'E6_ITEM');
 need(Array.isArray(o.evidence)&&o.evidence.length<=10&&o.evidence.every(x=>refs.has(x)),'E6_EVIDENCE');
 }return v;
}
export function fixture(index){return index===1?{question:'你想向谁表达谢意？',options:[]}:{question:null,options:[
 {id:'thanks',title:'先表达感谢',text:'我想向周禾道谢，暂时保留丹药。',reason:'回应致谢目标并保留物资。',evidence:['people:zhou'],unknown:'周禾是否在场及其意愿未知。',support:'仅交流意图，不执行。',kind:'conversation',targetId:'zhou',itemId:null},
 {id:'ask',title:'了解对方需要',text:'我想先问周禾需要什么帮助。',reason:'不立即消耗丹药。',evidence:['people:zhou','items:medicine'],unknown:'对方是否愿意交流未知。',support:'仅意图，不执行。',kind:'conversation',targetId:'zhou',itemId:null}]};}
