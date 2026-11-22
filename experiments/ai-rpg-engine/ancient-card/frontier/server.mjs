import {createServer} from 'node:http';
import {readFile,readdir} from 'node:fs/promises';
import {dirname,join,resolve,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {installCustomConfig} from '../../plugins/custom-config/host.mjs';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {emptySession,publicSession,play} from './session.mjs';

const cardDir=dirname(dirname(fileURLToPath(import.meta.url)));
const dist=resolve(cardDir,'../card-replica/dist');
const base='/rpg-app/ancient/chat/';
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.jpg':'image/jpeg','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2'};
export async function openHost(directory,{atlasQueries=false,placeRegister=false,storyMap=false,generate}={}){
  const records=await SessionRecords.open(directory,{scope:'ancient-frontier-v1',recoverDeadOwner:true});
  await records.reconcile();
  async function read(id){const r=await records.get(id);if(!r)throw Error('存档不存在');return r;}
  return {
    async list(){const files=(await readdir(join(directory,'records'))).filter(f=>/^[a-f0-9-]{36}\.json$/.test(f));
      return Promise.all(files.map(async f=>{const {data}=await read(f.slice(0,-5));return {id:data.id,name:data.name,revision:data.revision,turns:data.turns.length};}));},
    async create(){const s=emptySession(atlasQueries,placeRegister);if(storyMap)s.storyMap={factCursor:0};await records.put(s.id,null,s);return publicSession(s);},
    async get(id){return publicSession((await read(id)).data);},
    async send(id,request){
      const {data,hash}=await read(id),previous=data.revision;
      await play(data,request,generate);
      if(data.revision!==previous){
        try{await records.put(id,hash,data);}
        catch(e){
          if(e.message!=='OFFLINE_STORAGE_CONFLICT'&&e.code!=='OFFLINE_STORAGE_CONFLICT')throw e;
          const latest=(await read(id)).data;
          const prior=latest.requests[request.requestId];
          if(prior?.input!==request.input||prior?.revision!==request.revision)throw Error('回合版本已变更，请刷新');
          return publicSession(latest);
        }
      }
      return publicSession(data);
    },close:()=>records.close()
  };
}
async function jsonInput(req){
  let size=0;const chunks=[];
  for await(const chunk of req){size+=chunk.length;if(size>100000)throw Error('输入过长');chunks.push(chunk);}
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}
export async function preview(port,directory,options){
  if(options?.storyMap && !options.placeRegister)throw Error('地图剧情需要地点登记');
  const host=await openHost(directory,options);
  const assets=options?.storyMap?resolve(cardDir,'../.rpg03-work/story-map-dist'):dist;
  const server=createServer(async(req,res)=>{
    const json=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
    try{
      if(req.headers.host!=='127.0.0.1:'+port)return json(403,{error:'仅本机预览'});
      const url=new URL(req.url,'http://'+req.headers.host),p=url.pathname;
      if(p.startsWith(base+'api/')){
        const route=p.slice((base+'api/').length);
        if(req.method==='POST'&&(req.headers.origin&&req.headers.origin!==url.origin||!req.headers['content-type']?.startsWith('application/json')))return json(403,{error:'仅接受本机页面的 JSON 操作'});
        if(req.method==='GET'&&route==='status')return json(200,{providerEnabled:false,frontier:true});
        if(req.method==='GET'&&route==='plugins')return json(200,{plugins:[],revision:0});
        if(route==='sessions'&&req.method==='GET')return json(200,await host.list());
        if(route==='sessions'&&req.method==='POST'){await jsonInput(req);return json(201,await host.create());}
        const m=/^sessions\/([a-f0-9-]{36})(\/send)?$/.exec(route);
        if(m&&!m[2]&&req.method==='GET')return json(200,await host.get(m[1]));
        if(m&&m[2]&&req.method==='POST')return json(200,await host.send(m[1],await jsonInput(req)));
        return json(404,{error:'独立预览未启用此入口'});
      }
      if(req.method!=='GET')return json(405,{error:'不支持'});
      if(p==='/'){res.writeHead(302,{Location:base});return res.end();}
      let file;
      if(p===base)file=join(assets,'index.html');
      else if(p==='/rpg-app/ancient/')file=join(cardDir,'index.html');
      else if(p==='/rpg-app/ancient/landscape.jpg')file=join(cardDir,'landscape.jpg');
      else if(p.startsWith(base+'assets/')&&/^[-.\w]+$/.test(p.slice((base+'assets/').length)))file=join(assets,'assets',p.slice((base+'assets/').length));
      if(!file)return json(404,{error:'资源不存在'});
      const bytes=p===base?(await readFile(file,'utf8')).replace('<html lang=', options?.storyMap?'<html data-ancient-frontier="1" data-story-map="1" lang=':'<html data-ancient-frontier="1" lang='):await readFile(file);res.writeHead(200,{'Content-Type':mime[extname(file)]||'application/octet-stream','X-Content-Type-Options':'nosniff'});res.end(bytes);
    }catch(e){json(e.code==='ENOENT'||e.message==='存档不存在'?404:409,{error:e.message});}
  });
  if(options?.storyMap)installCustomConfig(server,['http://127.0.0.1:'+port],directory);
  try{await new Promise((ok,fail)=>server.once('error',fail).listen(port,'127.0.0.1',ok));}
  catch(e){await host.close();throw e;}
  return async()=>{await new Promise(ok=>server.close(ok));await host.close();};
}
if(process.argv[1]&&pathToFileURL(resolve(process.argv[1])).href===import.meta.url){
  const port=Number(process.env.FRONTIER_PORT||18974);
  const atlasQueries=process.env.ANCIENT_ATLAS_QUERIES==='1';
  const storyMap=process.env.ANCIENT_STORY_MAP==='1';
  const placeRegister=storyMap || process.env.ANCIENT_PLACE_REGISTER==='1';
  const directory=storyMap?'ancient-story-map':placeRegister?'ancient-place-register':atlasQueries?'ancient-atlas-chat':'ancient-frontier-v1';
  const close=await preview(port,resolve(cardDir,'../.rpg03-work',directory),{atlasQueries,placeRegister,storyMap});
  console.log('http://127.0.0.1:'+port+base);
  process.once('SIGINT',async()=>{await close();process.exit(0);});
  process.once('SIGTERM',async()=>{await close();process.exit(0);});
}
