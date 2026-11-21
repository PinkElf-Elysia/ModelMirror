import {initialize,agentView,command,turn} from './agent.mjs';
import http from 'node:http';import {readFile} from 'node:fs/promises';import {randomUUID} from 'node:crypto';import {SessionRecords} from '../../authoring/session-storage.mjs';import {opening,newRun,project,apply} from './world.mjs';
const port=Number(process.argv[2]||18968), records=await SessionRecords.open(process.argv[3],{scope:'ancient-map-july',recoverDeadOwner:true});
const assets={'/':['index.html','text/html'],'/opening':['../../ancient-card/index.html','text/html'],'/landscape.jpg':['../../ancient-card/landscape.jpg','image/jpeg']};
http.createServer(async(req,res)=>{function json(code,value){res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));}
try{if(req.headers.host!=='127.0.0.1:'+port)throw Error('无效来源');const path=req.url.split('?')[0];
if(req.method==='GET'&&Object.hasOwn(assets,path)){const [file,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8'});return res.end(await readFile(new URL(file,import.meta.url)));}
let data={};if(req.method==='POST'){if(req.headers['x-map-preview']!=='1')throw Error('无效请求');let body='';req.setEncoding('utf8');for await(const part of req){body+=part;if(body.length>16000)throw Error('请求过长');}data=JSON.parse(body);}
if(req.method==='POST'&&path==='/runs'){const id=randomUUID();await records.put(id,null,(process.argv.includes('--agent')?initialize:s=>s)(newRun(opening(data.preset,data.scroll))));return json(201,{id});}
const fork=path.match(/^\/runs\/([a-f0-9-]{36})\/branch$/);if(req.method==='POST'&&fork){const parent=await records.get(fork[1]);if(!parent||parent.hash!==data.revision)throw Error('请重读原档');const id=randomUUID();await records.put(id,null,{...(parent.data.agent?initialize(newRun(parent.data.person)):newRun(parent.data.person)),openingOf:fork[1]});return json(201,{id});}
const id=path.match(/^\/runs\/([a-f0-9-]{36})$/)?.[1];if(!id)return json(404,{});const row=await records.get(id);if(!row)return json(404,{});const s=row.data;
if(req.method==='POST'){if(data.revision!==row.hash)throw Error('记录已变化，请重读');if(s.agent){if(data.action==='turn')await turn(s,data.input);else command(s,data.action);}else apply(s,data.action);await records.put(id,row.hash,s);}else if(req.method!=='GET')return json(405,{});
const saved=await records.get(id);json(200,{id,revision:saved.hash,...(saved.data.agent?agentView(saved.data):project(saved.data))});}catch(e){json(400,{error:e.message});}}).listen(port,'127.0.0.1');
