import {createHost} from '../server.mjs';
import {writeFile,mkdir} from 'node:fs/promises';
const root=new URL('../.local/independent-20260917/',import.meta.url);
await mkdir(root,{recursive:true});
const directory=new URL('ui-sessions/',root).pathname.replace(/^\/([A-Z]:)/,'$1');
const h=await createHost({port:18412,directory});
await writeFile(new URL('preview.pid',root),String(process.pid));
console.log('Independent offline UI http://127.0.0.1:'+h.port+'; separate browser origin and session directory; provider disabled');
