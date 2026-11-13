import test from 'node:test';import assert from 'node:assert/strict';import{createClientMeasurements}from'../card-replica/src/shop-client-measurement.mjs';
test('browser timing is monotonic, first-only, reload-safe and separate from authoritative state',()=>{
 let saved='',time=100;const storage={getItem:()=>saved,setItem:(_,v)=>{saved=v;}},clock=()=>time;
 const m=createClientMeasurements(storage,clock,()=> 'synthetic');m.start('s','e');time=120;m.mark('s','e','firstFeedbackMs');time=150;m.mark('s','e','firstFeedbackMs');
 m.mark('s','e','firstBodyMs',false);assert.equal(m.export('s')[0].firstBodyMs,null);
 time=250;m.mark('s','e','firstBodyMs');m.mark('s','e','bodyCompleteMs');assert.equal(m.export('s')[0].firstFeedbackMs,20);assert.equal(m.export('s')[0].firstBodyMs,150);
 const reopened=createClientMeasurements(storage,clock);reopened.mark('s','e','allStagesCompleteMs');assert.equal(reopened.export('s')[0].allStagesCompleteMs,null);assert.equal(reopened.export('s')[0].interrupted,true);assert.equal(reopened.export('s')[0].authoritative,false);
});
