import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startControlledWorkbench} from './controlled-workbench.mjs';
import {CONTROL_CATALOG_FORMAT} from './model-control.mjs';

test('operator can open and reopen the workbench with zero allowance, using catalog reads only',async t=>{
  const calls=[],serviceToken='synthetic-local-control-token-for-test-only';
  const control=http.createServer((req,res)=>{
    calls.push([req.method,req.url]);
    assert.equal(req.headers.authorization,'Bearer '+serviceToken);
    res.writeHead(200,{'Content-Type':'application/json'});
    res.end(JSON.stringify({format:CONTROL_CATALOG_FORMAT,source:'control-plane-cache',catalogId:'fixture',revision:'b'.repeat(64),models:[]}));
  });
  await new Promise(resolve=>control.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>control.close(resolve)));
  const parent=fileURLToPath(new URL('../.rpg04-work/controlled-workbench/',import.meta.url));
  await mkdir(parent,{recursive:true});
  const directory=await mkdtemp(join(parent,'run-')),origin='http://127.0.0.1:'+control.address().port;
  const opts={directory,origin,serviceToken};
  for(let n=0;n<2;n++){
    const host=await startControlledWorkbench(opts);
    try{
      const page=await(await fetch(host.origin)).text();
      assert.match(page,/data-execution="controlled"/);assert.ok(!page.includes(serviceToken));
      const joint=await(await fetch(host.origin+'/rpg-app/api/authoring/joint')).json();
      assert.equal(joint.enabled,true);assert.equal(joint.realProvider,true);
    }finally{await host.close();}
  }
  await assert.rejects(startControlledWorkbench({...opts,limit:1}),e=>e.code==='JOINT_ALLOWANCE_CHANGED');
  assert.deepEqual(calls,Array.from({length:3},()=>['GET','/api/rpg/v1/authoring/models']));
});
