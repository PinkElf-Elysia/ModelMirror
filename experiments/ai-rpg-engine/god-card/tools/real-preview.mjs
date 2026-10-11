// B4 isolated controlled UI. No automatic dispatch; four-call God-only ledger.
import http from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join,resolve,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../../studio/server.mjs';
import {godDocumentNavigation} from '../lib/ui.mjs';
const repo=fileURLToPath(new URL('../../../../',import.meta.url));
const directory=join(repo,'experiments/ai-rpg-engine/.rpg04-work/god-card/b4-real/data');
await mkdir(directory,{recursive:true});
const backend=18511,port=18512;
const auth=JSON.parse(await readFile(join(directory,'../AUTHORIZATION.json'),'utf8'));
if(auth.id!=='rpg-god-b4-20261010'||auth.limit!==4||!auth.noRetry||auth.automaticCertification!==false)throw Error('AUTHORIZATION_MISMATCH');
let limit=auth.limit;
try {const extra=JSON.parse(await readFile(join(directory,'../R3-EXTRA-AUTHORIZATION.json'),'utf8'));if(extra.authorizationId!=='god-r3-extra-one-20261011'||extra.originalAuthorizationId!==auth.id||extra.additionalCalls!==1||extra.totalLimit!==5||extra.manualDispatchOnly!==true)throw Error('EXTRA_AUTHORIZATION_MISMATCH');limit=extra.totalLimit;}catch(error){if(error.code!=='ENOENT')throw error;}
let budgetExemptions=[];
try {const extra=JSON.parse(await readFile(join(directory,'../R3-LENGTH-AUTHORIZATION.json'),'utf8'));if(extra.authorizationId!=='god-r3-length-two-20261011'||extra.originalAuthorizationId!==auth.id||extra.additionalCalls!==2||extra.previousLimit!==5||extra.totalLimit!==7||extra.manualDispatchOnly!==true||limit!==5)throw Error('LENGTH_AUTHORIZATION_MISMATCH');limit=extra.totalLimit;}catch(error){if(error.code!=='ENOENT')throw error;}
try {const extra=JSON.parse(await readFile(join(directory,'../R4-SUFFIX-AUTHORIZATION.json'),'utf8'));if(extra.authorizationId!=='god-r4-suffix-two-20261011'||extra.originalAuthorizationId!==auth.id||extra.additionalCalls!==2||extra.previousLimit!==7||extra.totalLimit!==9||extra.manualDispatchOnly!==true||limit!==7)throw Error('SUFFIX_AUTHORIZATION_MISMATCH');limit=extra.totalLimit;}catch(error){if(error.code!=='ENOENT')throw error;}
try {
 const amendment=JSON.parse(await readFile(join(directory,'../R3-BUDGET-AMENDMENT.json'),'utf8'));
 if(amendment.authorizationId!=='god-r3-user-exemption-20261011'||amendment.originalAuthorizationId!==auth.id||amendment.limit!==4||!Array.isArray(amendment.exemptions)||amendment.exemptions.length!==1||amendment.exemptions[0].authorizationId!==amendment.authorizationId)throw Error('AUTHORIZATION_AMENDMENT_MISMATCH');
 budgetExemptions=amendment.exemptions;
} catch(error){if(error.code!=='ENOENT')throw error;}
try {
 const amendment=JSON.parse(await readFile(join(directory,'../R3-QUOTA-AMENDMENT.json'),'utf8'));
 if(amendment.authorizationId!=='god-r3-quota-exemption-20261011'||amendment.originalAuthorizationId!==auth.id||amendment.limit!==5||amendment.controlLimit!==100||amendment.exemptions?.length!==1||amendment.exemptions[0].authorizationId!==amendment.authorizationId)throw Error('QUOTA_AMENDMENT_MISMATCH');
 budgetExemptions.push(...amendment.exemptions);
} catch(error){if(error.code!=='ENOENT')throw error;}
try {
 const amendment=JSON.parse(await readFile(join(directory,'../R4-TRUNCATION-AMENDMENT.json'),'utf8'));
 if(amendment.authorizationId!=='god-r4-terminal-exemption-20261011'||amendment.originalAuthorizationId!==auth.id||amendment.limit!==9||limit!==9||amendment.exemptions?.length!==1||amendment.exemptions[0].authorizationId!==amendment.authorizationId)throw Error('TERMINAL_AMENDMENT_MISMATCH');
 budgetExemptions.push(...amendment.exemptions);
} catch(error){if(error.code!=='ENOENT')throw error;}
try {const extra=JSON.parse(await readFile(join(directory,'../R4-CONTINUATION-AUTHORIZATION.json'),'utf8'));if(extra.authorizationId!=='god-r4-continuation-two-20261011'||extra.originalAuthorizationId!==auth.id||extra.additionalCalls!==2||extra.previousLimit!==9||extra.totalLimit!==11||extra.manualDispatchOnly!==true||extra.noRetry!==true||limit!==9)throw Error('CONTINUATION_AUTHORIZATION_MISMATCH');limit=extra.totalLimit;}catch(error){if(error.code!=='ENOENT')throw error;}
try {const extra=JSON.parse(await readFile(join(directory,'../R5-RENDER-AUTHORIZATION.json'),'utf8'));if(extra.authorizationId!=='god-r5-render-one-20261011'||extra.originalAuthorizationId!==auth.id||extra.additionalCalls!==1||extra.previousLimit!==11||extra.totalLimit!==12||extra.manualDispatchOnly!==true||extra.noRetry!==true||limit!==11)throw Error('RENDER_AUTHORIZATION_MISMATCH');limit=extra.totalLimit;}catch(error){if(error.code!=='ENOENT')throw error;}
try {const extra=JSON.parse(await readFile(join(directory,'../R5-RETEST-AUTHORIZATION.json'),'utf8'));if(extra.authorizationId!=='god-r5-retest-one-20261011'||extra.originalAuthorizationId!==auth.id||extra.additionalCalls!==1||extra.previousLimit!==12||extra.totalLimit!==13||extra.agentDispatchLimit!==1||extra.noRetry!==true||limit!==12)throw Error('RETEST_AUTHORIZATION_MISMATCH');limit=extra.totalLimit;}catch(error){if(error.code!=='ENOENT')throw error;}
const host=await start({port:backend,directory,cardDist:join(repo,'experiments/ai-rpg-engine/.rpg04-work/god-card/r5-render/dist'),godCard:true,godPlugins:true,godControl:{baseURL:'http://127.0.0.1:8000/',serviceToken:process.env.RPG_S2S_TOKEN,enabled:true,limit,budgetExemptions},enabled:false,limit:0,origins:[`http://127.0.0.1:${backend}`,`http://127.0.0.1:${port}`]});
if(!host.plugins){host.server.close();throw Error('GOD_PLUGIN_HOST_REQUIRED');}
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{void host.plugins.close().finally(()=>host.server.close());});
const root=join(repo,'client/dist');
const proxy=http.createServer(async(req,res)=>{try{
 const pathname=new URL(req.url,'http://preview').pathname;
 if(await godDocumentNavigation(req,res,join(repo,'experiments/ai-rpg-engine/.rpg04-work/god-card/r5-render/dist')))return;
 if(pathname.startsWith('/rpg-app/')){const r=http.request({hostname:'127.0.0.1',port:backend,path:req.url,method:req.method,headers:req.headers},up=>{res.writeHead(up.statusCode,up.headers);up.pipe(res);});r.on('error',()=>res.writeHead(502).end());req.pipe(r);return;}
 if(req.method!=='GET'||pathname.startsWith('/api/')){res.writeHead(404).end('{}');return;}
 const file=resolve(root,pathname.replace(/^\/+/,''));
 if(file!==root&&!file.startsWith(root+'/')&&!file.startsWith(root+'\\')){res.writeHead(403).end();return;}
 let bytes,type;try{if(!extname(file))throw Error();bytes=await readFile(file);type=extname(file);}catch{bytes=await readFile(join(root,'index.html'));type='.html';}
 res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.jpg':'image/jpeg','.svg':'image/svg+xml'}[type]||'application/octet-stream'),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
 res.end(bytes);
 }catch{res.writeHead(500).end('Candidate unavailable');}});
await new Promise((ok,no)=>{proxy.once('error',no);proxy.listen(port,'127.0.0.1',ok);});
await writeFile(join(directory,'host-owner.json'),JSON.stringify({pid:process.pid,backend,ui:port,realLimit:limit,sharedServicesChanged:false,source:'god-card/tools/real-preview.mjs'},null,2));
console.log('God B4 controlled UI http://127.0.0.1:'+port+'/rpg — no automatic dispatch.');
