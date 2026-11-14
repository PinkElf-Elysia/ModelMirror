import {readFile} from 'node:fs/promises';
import {loadAdapter,effectiveSystem,DELTA_INSTRUCTION} from '../../local-state/adapter.mjs';
import {hash,need} from '../contracts.mjs';
import {material} from './assemble.mjs';
export const QUALITY=`保持以下行为约束，但不输出检查过程或逐项分析：确认界域、时间、地点、年龄、身份及境界；移动和时间跳跃须合理，大跳跃需玩家确认。玩家与 NPC 只知道有来源的信息，不能泄漏秘密、未暴露能力或未来事件。人物性格、阵营、关系保持连续，不替玩家决定。
实力提升、学习与关系发展须有过程；不能无理由跨境界取胜。同境界考虑状态、已知能力、相克和情报差；差一大境界须有明确特殊条件，差两大境界以上不可正面力敌。灵力、法宝有上限和代价，突破有风险；低境界的无力感、资源争夺和宗门生态应合理呈现。亲密门槛仍是关系≥80且信任≥60，NPC 不主动发情。不造额外计算公式。
正文保留动作、对话、心理、环境与必要术法细节，感官细节和人物反应有因果，避免重复词句与强逼悬念。记忆区沿原卡要求，未接管展示保留原结构。玩家人格必须与开局一致。宿主字段只由增量提交维护，不重算或复制完整面板。`;
function ranges(text,start=0){
 const points=[0,...[...text.matchAll(/^#{1,2} [^\r\n]+/gm)].map(m=>m.index).filter(i=>i>0),text.length];
 return points.slice(0,-1).map((a,i)=>({start:start+a,end:start+points[i+1],title:text.slice(a,points[i+1]).split(/\r?\n/)[0]||'preamble',text:text.slice(a,points[i+1])}));
}
export function classifyReference(section,index){return index.sections.find(s=>s.hash===hash(section.text)&&s.title===section.title)?.disposition==='conditional-reference'?'conditional-reference':'unclassified-preserved';}
export async function cultivationSource(){
 const legacy=await loadAdapter(),raw=legacy.original,boundary=raw.indexOf('【东域（总览·校准版）】');need(boundary>0,'CONTEXT_SOURCE_ANCHOR');
 const index=JSON.parse(await readFile(new URL('./reference-index.json',import.meta.url),'utf8'));need(index.sourceHash===legacy.binding.sourceHash,'CONTEXT_REFERENCE_INDEX');
 const global=raw.slice(0,boundary),staticRanges=ranges(raw.slice(boundary),boundary);
 let effective=effectiveSystem(global).replace(DELTA_INSTRUCTION,'');
 effective=effective.replace(/\*\*思维链强制校验\*\*：[\s\S]*?```Checking[\s\S]*?```/, '信息来源约束：只有亲历或通过合法渠道获取的信息可进入人物认知；保留具体来源及时间地点，无来源则保持未知，不输出检查过程。');
 const a=effective.indexOf('## 思维链执行规则'),b=effective.indexOf('## 状态栏输出规则',a);need(a>=0&&b>a,'CONTEXT_CHECK_ANCHOR');
 effective=effective.slice(0,a)+'## 叙事质量约束\n\n'+QUALITY+'\n\n'+effective.slice(b);
 effective=effective.replace('2. 【思维链·质量管控】','2. 不输出检查区或分析过程；直接进入正文').replace('【校验】PHASE 1强制检查：玩家设定 → 本轮对话/行为符合度 → 不符合必须修正','【人格约束】正文中的行为与对话应符合玩家设定，不输出校验过程。');
 const globalRanges=ranges(global),audit=[...globalRanges,...staticRanges].map((s,i)=>({id:'source-'+i,title:s.title,start:s.start,end:s.end,hash:hash(s.text),bytes:Buffer.byteLength(s.text),disposition:s.start>=boundary?classifyReference(s,index):/思维链/.test(s.title)?'check-output-replaced-with-behavior-constraints':/信息迷雾/.test(s.title)?'check-output-replaced-with-source-constraints':/金库|状态栏|回复结构/.test(s.title)?'host-owned-output-adapted':'required-preserved'}));
 const sourceRange=title=>{const found=globalRanges.find(x=>x.title===title||title==='## 本地档案留存'&&x.title.startsWith('## ⚓ 金库机制'));if(found)return [found.start,found.end];if(title==='## 叙事质量约束')return [globalRanges.find(x=>x.title==='## 思维链执行规则').start,globalRanges.find(x=>x.title==='## 思维链·质量管控').end];return [0,boundary];};
 const sections=ranges(effective),source={path:'cultivation-card/author-system.txt',hash:legacy.binding.sourceHash};
 const refs=staticRanges.map((s,i)=>({...s,id:'reference-'+i}));
 const find=title=>{const v=refs.find(r=>r.title.includes(title));need(v,'CONTEXT_REFERENCE_ANCHOR');return v.id;};
 const east=refs[0].id,places=find('四、凡界场所'),sects=find('六、东域宗门');
 return {legacy,audit,source,rawBytes:Buffer.byteLength(raw),materials(scope){return [
  ...sections.map((s,i)=>material(scope,'rule-'+i,/正文|描写|人格|质量/.test(s.title)?'narration':'global-rule','instruction',s.text,{...source,range:sourceRange(s.title),derivedTitle:s.title,transformation:'local-state/1 + context/4'}, {required:true})),
  material(scope,'delta-contract','output-contract','instruction',DELTA_INSTRUCTION,{path:'local-state/adapter.mjs',hash:hash(DELTA_INSTRUCTION)},{required:true}),
  ...refs.map(s=>material(scope,s.id,'world-reference','reference',s.text,{...source,range:[s.start,s.end],title:s.title},{required:classifyReference(s,index)!=='conditional-reference',dependencies:s.id===sects||s.id===places?[east]:[]}))
 ];},bindings:{'scene:qingyun':[sects,places],'scene:qingniu':[places],'object:qingyun':[sects],'region:east':[east]}};
}
