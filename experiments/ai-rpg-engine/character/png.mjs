import {deflateSync} from 'node:zlib';
import {CARD_LIMITS,problem} from './card.mjs';
const signature=Buffer.from([137,80,78,71,13,10,26,10]);
export function crc32(bytes){let c=0xffffffff;for(const b of bytes){c^=b;for(let i=0;i<8;i++)c=(c>>>1)^((c&1)?0xedb88320:0);}return (c^0xffffffff)>>>0;}
export function chunk(type,data){const b=Buffer.alloc(data.length+12);b.writeUInt32BE(data.length);b.write(type,4,4,'ascii');data.copy(b,8);b.writeUInt32BE(crc32(b.subarray(4,-4)),b.length-4);return b;}
export function pngChunks(input){
 const b=Buffer.from(input);if(b.length>CARD_LIMITS.png)problem('CHARACTER_FILE_SIZE');
 if(!b.subarray(0,8).equals(signature))problem('CHARACTER_PNG_SIGNATURE');
 const chunks=[];let offset=8,end=false,idat=false;
 while(offset<b.length){
  if(offset+12>b.length)problem('CHARACTER_PNG_TRUNCATED');
  const length=b.readUInt32BE(offset),next=offset+12+length;
  if(next>b.length)problem('CHARACTER_PNG_TRUNCATED');
  const type=b.toString('ascii',offset+4,offset+8),data=b.subarray(offset+8,next-4);
  if(!/^[A-Za-z]{4}$/.test(type)||crc32(b.subarray(offset+4,next-4))!==b.readUInt32BE(next-4))problem('CHARACTER_PNG_CRC');
  if(!chunks.length&&(type!=='IHDR'||length!==13))problem('CHARACTER_PNG_STRUCTURE');
  if(type==='IHDR'){if(chunks.length||!data.readUInt32BE(0)||!data.readUInt32BE(4)||data.readUInt32BE(0)*data.readUInt32BE(4)>40000000)problem('CHARACTER_IMAGE_DIMENSIONS');}
  if(type==='IHDR'&&(!({0:[1,2,4,8,16],2:[8,16],3:[1,2,4,8],4:[8,16],6:[8,16]}[data[9]]||[]).includes(data[8])||data[10]!==0||data[11]!==0||data[12]>1))problem('CHARACTER_PNG_STRUCTURE');
  if(type==='IDAT')idat=true;
  chunks.push({type,data:Buffer.from(data)});offset=next;
  if(type==='IEND'){if(length||offset!==b.length||!idat)problem('CHARACTER_PNG_STRUCTURE');end=true;break;}
 }
 if(!end)problem('CHARACTER_PNG_TRUNCATED');return chunks;
}
export function textKey(c){if(c.type!=='tEXt')return null;const n=c.data.indexOf(0);return n<1?null:c.data.toString('latin1',0,n);}
export function metadata(chunks){return chunks.filter(c=>['chara','ccv3'].includes(textKey(c)?.toLowerCase())).map(c=>({key:textKey(c).toLowerCase(),base64:c.data.subarray(c.data.indexOf(0)+1).toString('latin1')}));}
export function decode64(value,max=CARD_LIMITS.json){
 if(typeof value!=='string'||value.length>Math.ceil(max/3)*4||value.length%4!==0||!/^[A-Za-z0-9+/]*={0,2}$/.test(value))problem('CHARACTER_BASE64_INVALID');
 const b=Buffer.from(value,'base64');if(b.length>max)problem('CHARACTER_FILE_SIZE');if(b.toString('base64')!==value)problem('CHARACTER_BASE64_INVALID');return b;
}
export function portrait(chunks){return Buffer.concat([signature,...chunks.filter(c=>c.type!=='tEXt'&&c.type!=='zTXt'&&c.type!=='iTXt'&&c.type!=='eXIf'&&!['acTL','fcTL','fdAT'].includes(c.type)).map(c=>chunk(c.type,c.data))]);}
export function placeholder(){
 const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(1);ihdr.writeUInt32BE(1,4);ihdr[8]=8;ihdr[9]=6;
 return Buffer.concat([signature,chunk('IHDR',ihdr),chunk('IDAT',deflateSync(Buffer.from([0,68,80,93,255]))),chunk('IEND',Buffer.alloc(0))]);
}
export function exportPng(card,image=placeholder()){
 const clean=pngChunks(portrait(pngChunks(image))),json=Buffer.from(JSON.stringify(card),'utf8');
 if(json.length>CARD_LIMITS.json)problem('CHARACTER_FILE_SIZE');
 const data=Buffer.from('chara\0'+json.toString('base64'),'latin1');
 return Buffer.concat([signature,...clean.flatMap(c=>c.type==='IEND'?[chunk('tEXt',data),chunk(c.type,c.data)]:[chunk(c.type,c.data)])]);
}
