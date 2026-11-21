export const places=['北京','山海关','宁远','锦州'];
export const date='崇祯元年七月';
const names={soldier:'宁远驻军士兵',court:'崇祯帝朱由检'};
export function opening(preset,scroll=''){
 if(preset in names && Object.hasOwn(names,preset))return {name:names[preset],role:preset,scroll:'【切片预设】'+names[preset]};
 if(preset!=='scroll'||typeof scroll!=='string'||scroll.length>12000)throw Error('无效命书');
 const field=k=>scroll.match(new RegExp('^【'+k+'】(.+)$','m'))?.[1]?.trim();
 const route=field('人生路线');if(!['帝王之路','青史留名','芸芸众生','自定义世界'].includes(route))throw Error('请先生成命书');
 const name=field('帝王')||field('历史人物')||field('姓名')||'自定义人物';
 return {name,role:route==='帝王之路'&&/^(崇祯帝朱由检|朱由检|崇祯帝)$/.test(name)?'court':'observer',scroll};
}
export function newRun(person){return {version:1,person,step:0,report:null,control:'明',log:[]};}
export function options(s){
 if(s.step===0)return s.person.role==='soldier'?{quick:'立即上报，注明失城传闻未核',verify:'先查值守记录，再上报'}:{wait:'等待下一封消息'};
 if(s.step===1)return {deliver:'推进至下一次递送'};
 if(s.step===2&&s.person.role==='court')return {verify:'批示复核，维持守方',transfer:'模拟决策：同意宁远守方向后金移交城防'};
 if(s.step===2)return {finish:'查阅送达回执'};
 return {};
}
export function apply(s,action){
 if(!Object.hasOwn(options(s),action))throw Error('该行动当前不可用');
 const role=s.person.role;
 if(s.step===0&&role==='soldier')s.report=action;
 if(s.step===0)s.log.push(role==='soldier'?(action==='quick'?'快报已发，文中保留“失城未核”。':'你查过值守记录，只将营中鼓噪写入报告，错过了头班递送。'):'你等候下一班递报。');
 if(s.step===1)s.log.push(role==='soldier'?(s.report==='quick'?'快报先到，值守官发回补证要求。':'核报晚一班送达，值守官按营变登记。'):role==='court'?'关报抵京，奏称宁远营中因欠饷鼓噪。':'公开邸报传来宁远营变消息。');
 if(s.step===2){if(action==='transfer'){s.control='后金';s.log.push('本档推演：交割文书与接防回执均已送抵，宁远控制方改为后金。');}else s.log.push(role==='court'?'复核批示已送出，当前控制记录沿用。':role==='soldier'?(s.report==='quick'?'你领到补证差事，快报与查问一并存档。':'值守官签收核报，你的递报差事结束。'):'你将邸报收入行记。');}
 s.step++;
}
export function project(s){
 const soldier=s.person.role==='soldier', court=s.person.role==='court';
 const sources=['开局登记 · 明廷防区'];
 if(soldier)sources.push('亲见 · 营中聚集','同袍口述 · “城已失”，未经核实');
 if(s.step>=1&&soldier&&s.report==='verify')sources.push('值守记录 · 驻军仍值守');
 if(s.step>=2)sources.push(court?'抵京关报 · 宁远兵变':soldier?'值守官回执 · 报告已收':'公开邸报 · 宁远兵变');
 if(s.control!=='明')sources.push('本档模拟 · 接防回执确认交割');
 return {date,person:s.person,step:s.step,log:s.log,options:options(s),places:places.map(name=>({name,control:name==='宁远'?s.control:'明',sources:name==='宁远'?sources:[name==='山海关'&&s.step===1&&soldier?'递报簿 · 本班报告经关转递':'开局登记 · 明廷防区']}))};
}
