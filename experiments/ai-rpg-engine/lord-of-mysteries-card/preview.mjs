import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {assemble} from './assembly.mjs';

const entry = '/rpg/lord-of-mysteries/';
const assets = new Map([
  [entry, ['index.html','text/html; charset=utf-8']],
  [entry+'world-map.jpg', ['world-map.jpg','image/jpeg']],
  [entry+'fog-background.jpg', ['fog-background.jpg','image/jpeg']],
]);
const server = createServer(async (req,res) => {
  const origin = 'http://127.0.0.1:'+server.address().port;
  try {
    const asset = assets.get(req.url);
    if (req.method==='GET' && asset) {
      const bytes = await readFile(new URL('./public/'+asset[0],import.meta.url));
      res.writeHead(200,{'Content-Type':asset[1],'X-Content-Type-Options':'nosniff'});
      return res.end(bytes);
    }
    if (req.method==='POST' && req.url===entry+'prepare' && req.headers.origin===origin) {
      const chunks=[];
      let length=0;
      for await (const chunk of req) {
        length+=chunk.length;
        if(length>256*1024){res.writeHead(413);return res.end();}
        chunks.push(chunk);
      }
      const {messages}=assemble({input:Buffer.concat(chunks).toString('utf8')});
      res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});
      return res.end(JSON.stringify({prepared:true,characters:Array.from(messages[1].content).length}));
    }
    res.writeHead(404);res.end();
  } catch {res.writeHead(400);res.end();}
});
server.listen(0,'127.0.0.1',()=>console.log('http://127.0.0.1:'+server.address().port+entry));
