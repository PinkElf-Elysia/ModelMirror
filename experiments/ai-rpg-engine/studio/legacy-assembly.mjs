import {readFileSync} from 'node:fs';
import {sha} from './provider.mjs';
const prompt=JSON.parse(readFileSync(new URL('./rpg05-prompt.json',import.meta.url),'utf8'));
if(sha(prompt.system)!==prompt.sha256)throw Error('FROZEN_PROMPT_DRIFT');
export function legacyMessages(initial,history,input){
 const {cardPackage:card,playerSetup:p}=initial,r=card.resources;
 const resolve=(choice,kind)=>choice.source==='custom'?choice.resource:r[kind].find(x=>x.id===choice.resourceRef);
 const text=x=>x?[x.displayName,x.description||x.content||x.instruction].filter(Boolean).join('：'):'';
 const opening=r.openings.find(x=>x.id===p.opening.openingRef);if(!opening)throw Error('OPENING_MISSING');
 const cardText=[card.package.displayName,card.package.description,text(resolve(p.world,'worlds')),text(opening),...(opening.styleRefs||[]).map(id=>text(r.styles.find(x=>x.id===id))),...(opening.worldbookRefs||[]).map(id=>text(r.worldbookEntries.find(x=>x.id===id)))].filter(Boolean).join('\n\n');
 const setup=[...Object.entries(p.character).map(([k,v])=>k+'：'+(Array.isArray(v)?v.join('、'):v)), '身份：'+text(resolve(p.currentIdentity,'identities')), '背景：'+p.inherentBackgrounds.map(x=>text(resolve(x,'backgrounds'))).join('；'),'物品：'+p.possessions.map(x=>text(resolve(x.resource,'items'))+' × '+x.quantity).join('；'),'天赋：'+p.talents.filter(x=>x.owned).map(x=>text(resolve(x.resource,'talents'))+(x.active?'（已启用）':'（未启用）')).join('；'),p.characterPower.status==='declared'?'能力：'+p.characterPower.rankLabel+' '+(p.characterPower.description||''):''].filter(Boolean).join('\n');
 return [{role:'system',content:prompt.system},{role:'user',content:'本局卡片玩法：\n'+cardText+'\n\n角色与开场：\n'+setup},...history,{role:'user',content:input}];
}
