import {hash,clone,requireValue} from './contracts.mjs';
export function createMockProvider({mode='success',delay=25}={}){
 const receipts=new Map();let calls=0;
 const catalog=[{id:'mock-narrator',name:'离线叙事样例',available:true,upperBoundMicros:0},{id:'mock-fast',name:'离线备用样例',available:true,upperBoundMicros:0}];
 return {mode:'simulated',get calls(){return calls;},catalog:async()=>clone(catalog),validate:async c=>requireValue(catalog.some(x=>x.id===c.id),'SHOP_MODEL_UNAVAILABLE'),
  async dispatch(request){
   calls++;await new Promise(resolve=>setTimeout(resolve,delay));
   const base={requestId:request.id,messagesHash:request.messagesHash,terminal:true,costMicros:0,usage:{inputTokens:null,outputTokens:null,source:'simulated-not-tokenized'},actualModel:request.model.id};
   const fail=mode==='fail-all'||mode==='fail-primary'&&request.model.id==='mock-narrator';
   let raw;
   if(request.purpose==='intent'){
    const text=JSON.parse(request.messages.at(-1).content).input;
    const names=[['面包','bread'],['饮用水','water'],['笔记本','notebook']],item=names.find(([n])=>text.includes(n));
    const number=text.match(/(?:买|购买)\s*([一二两三四五六七八九十\d]+)/)?.[1];
    const q=number?({'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}[number]??Number(number)):null;
    raw=JSON.stringify(item&&q?{kind:'purchase',productId:item[1],quantity:q}:{kind:'clarify',message:'你想买哪件商品、多少件？'});
   }else if(request.purpose==='memory'){raw=JSON.stringify({operations:[]});}
   else if(request.purpose.startsWith('summary-')){raw='时间:未知|地点:街角杂货店|角色:玩家、店主|情节:玩家在杂货店购买物品，与店主交谈。';}
   else{
    const text=request.messages.at(-1).content,marker='【宿主已确认事件】\n',facts=JSON.parse(text.slice(text.lastIndexOf(marker)+marker.length));
    raw=facts.outcome==='committed'?'店主将纸袋轻轻推过柜台，指尖在收银台边停了一下。“带着路上吃吧。”门口的风铃随着来客响起，你接过东西，侧身让开了窄窄的过道。午后的阳光落在门外，街道依旧热闹。':'店主抬起眼睛，耐心等你继续说下去。货架间透进一线阳光，你留意到柜台旁还摆着几本笔记本。';
   }
   const result=fail?{...base,status:'failed',safeToRetry:true,code:'SIMULATED_PROVIDER_FAILURE'}:{...base,status:'succeeded',raw};
   receipts.set(request.id,result);
   return mode==='unknown'?{status:'unknown'}:clone(result);
  },
  async lookup(request){return clone(receipts.get(request.id)||{status:'unknown'});}
 };
}
