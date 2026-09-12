import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { createRequire } from 'node:module';
const ui=fileURLToPath(new URL('../',import.meta.url)),work=path.resolve(ui,'../.rpg04-work');
test('HTTP refusal preserves its classification; missing responses and ambiguous replies stay unresolved',async t=>{
 const directory=await fs.mkdtemp(path.join(work,'rpg05-rejection-'));const outfile=path.join(directory,'api.cjs');
 t.after(async()=>{const actual=await fs.realpath(directory);assert.equal(path.dirname(actual),await fs.realpath(work));assert.ok(path.basename(actual).startsWith('rpg05-rejection-'));await fs.rm(actual,{recursive:true});});
 await build({entryPoints:[path.join(ui,'src/api.ts')],bundle:true,platform:'node',format:'cjs',outfile,logLevel:'silent'});
 const {command,canClearRejectedRequest}=createRequire(import.meta.url)(outfile);let calls=0;
 for(const [status,code,clear,absentClear] of [[422,'PLAYER_PREFIX_UNKNOWN',true,true],[422,'PLAYER_TEXT_INVALID',true,true],[422,'PLAYER_COMMAND_UNKNOWN',true,true],[400,'COMMAND_PAYLOAD_INVALID',true,true],[409,'REVISION_CONFLICT',false,true],[409,'OPERATION_ID_CONFLICT',false,false],[503,'HOST_UNAVAILABLE',false,false],[422,'UNKNOWN_ERROR',false,false]]) {
  t.mock.method(globalThis,'fetch',async()=>{calls++;return Response.json({error:code},{status});});
  let caught;try{await command('journey.generate',{id:'journey.test',operationId:'op.same',expectedRevision:1,text:'/unknown x'});}catch(error){caught=error;}
  assert.equal(caught.status,status);assert.equal(caught.code,code);assert.equal(canClearRejectedRequest(caught),clear);assert.equal(canClearRejectedRequest(caught,true),absentClear);
 }
 t.mock.method(globalThis,'fetch',async()=>{calls++;throw new TypeError('response lost');});
 let network;try{await command('journey.generate',{});}catch(error){network=error;}assert.equal(canClearRejectedRequest(network,true),false);assert.equal(calls,9);
 t.mock.method(globalThis,'fetch',async()=>new Response('not JSON',{status:422}));
 let invalid;try{await command('journey.generate',{});}catch(error){invalid=error;}assert.equal(canClearRejectedRequest(invalid,true),false);
});
