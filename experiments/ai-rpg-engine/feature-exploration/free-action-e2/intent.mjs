// Conservative E2 grounding, not a general language parser or execution authorization.
const escape=s=>s.replace(/[.*+?^$()|[\]{}\\]/g,'\\$&');
function number(s){
 if(/^\d+$/.test(s))return Number(s);
 const digit={一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9};
 if(digit[s])return digit[s];
 if(/^[一二三四五六七八九]?十[一二三四五六七八九]?$/.test(s)){const [a,b]=s.split('十');return (a?digit[a]:1)*10+(b?digit[b]:0);}
 return null;
}
export function intentEvidence(world,input){
 const refs=[],quantities=[];
 const text=input.text;
 for(const domain of ['items','abilities','relations'])for(const entry of world.state[domain]){
  if(text.includes(entry.name)||input.selection.includes(domain+':'+entry.id))refs.push(domain+':'+entry.id);
  if(domain!=='items')continue;
  const n='(?<![0-9.＋+－一二两三四五六七八九十百千万零点负-])([0-9]+|[一二两三四五六七八九十]+)',unit='(?:份|颗|粒|枚|个)',name=escape(entry.name);
  const expressions=[new RegExp(n+'\\s*'+unit+'?\\s*'+name,'g'),new RegExp(name+'\\s*(?:数量为|为|共|[:：])?\\s*'+n+'\\s*'+unit,'g')];
  const values=expressions.flatMap(re=>[...text.matchAll(re)].map(m=>number(m[1])));
  if(values.length&&values.every(v=>Number.isSafeInteger(v)&&v>0&&v===values[0]))quantities.push({itemId:entry.id,quantity:values[0]});
 }
 return {refs,quantities,containers:'not-modeled'};
}
export function intentIssues(world,input,value,quantityOverride=null){
 const evidence=intentEvidence(world,input),issues=[];
 if(value.refs.some(r=>!evidence.refs.includes(r.domain+':'+r.id)))issues.push('请明确行动涉及的人物或物品名称，或在界面选择对象；登记列表不是默认选择。');
 for(const r of value.requirements){
  const explicit=quantityOverride?.itemId===r.itemId?quantityOverride:evidence.quantities.find(q=>q.itemId===r.itemId);
  if(!explicit||explicit.quantity!==r.quantity)issues.push('请明确拟使用的物品名称和数量；未确认数量不进行预估。');
 }
 // Common affirmative container claims only, not exhaustive natural-language validation.
 const text=[value.goal,...value.steps].join('\n');
 if(/从[^。！？\n]{0,60}(?:囊|袋|包|箱|柜|容器)[^。！？\n]{0,20}(?:取|拿|掏)|(?:放入|装入|放进|装进)[^。！？\n]{0,30}(?:囊|袋|包|箱|柜|容器)|(?:放在|装在|存于|位于)[^。！？\n]{0,30}(?:囊|袋|包|箱|柜|容器)/.test(text))
  issues.push('当前未登记物品的容器关系，请只描述行动目标，不指定取放位置。');
 return [...new Set(issues)];
}
