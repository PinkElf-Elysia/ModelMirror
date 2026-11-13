const key='rpg-shop-client-measurements/1';
export function createClientMeasurements(storage,clock=()=>performance.now(),date=()=>new Date().toISOString()){
 const starts=new Map();
 const read=()=>{try{const data=JSON.parse(storage.getItem(key)||'[]');return Array.isArray(data)?data:[];}catch{return [];}};
 const write=rows=>{try{storage.setItem(key,JSON.stringify(rows.slice(-100)));}catch{/* Measurement failure must not block gameplay. */}};
 return {
  start(sessionId,eventId){const rows=read();if(rows.some(r=>r.sessionId===sessionId&&r.eventId===eventId))return;starts.set(sessionId+'/'+eventId,clock());rows.push({sessionId,eventId,startedAt:date(),source:'browser-render-observation',firstFeedbackMs:null,firstBodyMs:null,bodyCompleteMs:null,allStagesCompleteMs:null,interrupted:false});write(rows);},
  mark(sessionId,eventId,field,visible=true){if(!['firstFeedbackMs','firstBodyMs','bodyCompleteMs','allStagesCompleteMs'].includes(field)||!visible)return;
   const rows=read(),row=rows.find(r=>r.sessionId===sessionId&&r.eventId===eventId);if(!row||row[field]!==null)return;
   const start=starts.get(sessionId+'/'+eventId);if(start===undefined)row.interrupted=true;else row[field]=clock()-start;write(rows);
  },
  export(sessionId){return read().filter(r=>r.sessionId===sessionId).map(r=>({...r,authoritative:false,refreshPolicy:'missing timing after refresh remains null; never replace with a new start'}));}
 };
}
