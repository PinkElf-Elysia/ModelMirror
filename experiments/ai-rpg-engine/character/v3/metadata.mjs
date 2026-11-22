const languages=new Set(('aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu').split(' '));
export function upgradeCard(card,now=Date.now){
 if(card.spec!=='chara_card_v2')throw Error('只可从 V2 创建 V3 草稿');
 card.spec='chara_card_v3';card.spec_version='3.0';card.data.group_only_greetings??=[];
 card.data.creation_date??=Math.floor(now()/1000);
 for(const entry of card.data.character_book?.entries??[])entry.use_regex??=false;
}
export function metadataPanel(host,card,changed,locked){
 host.replaceChildren();if(card.spec!=='chara_card_v3')return;
 const doc=host.ownerDocument,d=card.data;
 const node=(tag,value='')=>{const e=doc.createElement(tag);e.textContent=value;return e;};
 const update=()=>{metadataPanel(host,card,changed,locked);changed();};
 function field(label,value,write,tag='textarea'){
  const l=node('label',label),e=node(tag);e.value=value;e.dataset.v3Write='';e.disabled=locked;e.oninput=()=>{write(e.value);changed();};l.append(e);host.append(l);return e;
 }
 function button(label,fn){const b=node('button',label);b.type='button';b.disabled=locked;b.dataset.v3Write='';b.onclick=fn;host.append(b);}
 field('角色昵称',d.nickname??'',v=>d.nickname=v,'input');
 for(const key of Object.keys(d.creator_notes_multilingual??{})){
  field('作者说明 · '+key,d.creator_notes_multilingual[key],v=>d.creator_notes_multilingual[key]=v);
  button('移除 '+key,()=>{delete d.creator_notes_multilingual[key];update();});
 }
 const l=node('label','添加说明语言'),code=node('input');code.placeholder='zh';code.disabled=locked;code.dataset.v3Write='';l.append(code);host.append(l);
 button('添加语言',()=>{const key=code.value.trim().toLowerCase();if(!languages.has(key)||Object.hasOwn(d.creator_notes_multilingual??{},key)){code.setCustomValidity('请使用尚未添加的 ISO 639-1 语言代码');code.reportValidity();return;}
  (d.creator_notes_multilingual??={})[key]='';update();});code.oninput=()=>code.setCustomValidity('');
 for(const [i,value] of d.group_only_greetings.entries()){
  field('群开场 '+(i+1),value,v=>d.group_only_greetings[i]=v);
  if(i)button('上移群开场 '+(i+1),()=>{const g=d.group_only_greetings;[g[i-1],g[i]]=[g[i],g[i-1]];update();});
  button('移除群开场 '+(i+1),()=>{d.group_only_greetings.splice(i,1);update();});
 }
 button('添加群开场',()=>{d.group_only_greetings.push('');update();});
 host.append(node('p','卡内来源 · '+(d.source?.join('、')||'未提供')));
 for(const [key,label] of [['creation_date','创建时间'],['modification_date','导出修改时间']]){
  const v=d[key],date=new Date(v*1000);
  host.append(node('p',label+' · '+(v===undefined?'未提供':v===0?'未知（0）':Number.isFinite(date.getTime())?date.toISOString():String(v))));
 }
}
