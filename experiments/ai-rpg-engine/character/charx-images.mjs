import {pngChunks} from './png.mjs';
import {charxLimits} from './charx-archive.mjs';
import {problem} from './card.mjs';

const invalid = () => problem('CHARACTER_CHARX_IMAGE_INVALID');
function dimensions(width,height,animated=false) {
  if (!width || !height || width*height>charxLimits.pixels) invalid();
  return {width,height,animated};
}

function jpeg(b) {
  if (b.length<12 || b.readUInt16BE()!==0xffd8 || b.readUInt16BE(b.length-2)!==0xffd9) invalid();
  let at=2, shape;
  while(at<b.length-2) {
    if(b[at++]!==0xff) invalid();
    while(b[at]===0xff) at++;
    const marker=b[at++];
    if(at+2>b.length) invalid();
    const size=b.readUInt16BE(at);
    if(size<2 || at+size>b.length-2) invalid();
    if([0xc0,0xc1,0xc2].includes(marker)) {
      if(size<8) invalid();
      shape=dimensions(b.readUInt16BE(at+5),b.readUInt16BE(at+3));
    }
    if(marker===0xda) {if(!shape || size<6 || at+size>=b.length-2) invalid();return shape;}
    at+=size;
  }
  invalid();
}

function webp(b) {
  if(b.length<20 || b.toString('latin1',0,4)!=='RIFF' || b.readUInt32LE(4)+8!==b.length || b.toString('latin1',8,12)!=='WEBP') invalid();
  let at=12,frame,canvas,animated=false;
  while(at<b.length) {
    if(at+8>b.length) invalid();
    const kind=b.toString('latin1',at,at+4), size=b.readUInt32LE(at+4);
    const data=at+8;at=data+size+(size%2);
    if(at>b.length) invalid();
    if(kind==='VP8X') {
      if(size!==10) invalid();
      canvas=dimensions(1+b.readUIntLE(data+4,3),1+b.readUIntLE(data+7,3));
      animated=Boolean(b[data]&2);
    } else if(kind==='VP8 ') {
      if(size<10 || b.readUIntBE(data+3,3)!==0x9d012a) invalid();
      frame=dimensions(b.readUInt16LE(data+6)&16383,b.readUInt16LE(data+8)&16383);
    } else if(kind==='VP8L') {
      if(size<5 || b[data]!==0x2f) invalid();
      const bits=b.readUInt32LE(data+1);
      frame=dimensions(1+(bits&16383),1+((bits>>>14)&16383));
    } else if(kind==='ANMF' || kind==='ANIM') animated=true;
  }
  if(animated && canvas) return {...canvas,animated:true};
  if(!frame || (canvas && (canvas.width!==frame.width || canvas.height!==frame.height))) invalid();
  return frame;
}

export function inspectImage(b,ext) {
  if(ext==='jpeg' || ext==='jpg') return jpeg(b);
  if(ext==='webp') return webp(b);
  if(ext!=='png') return null;
  let count=0;
  for(let at=8;at+12<=b.length;at+=12+b.readUInt32BE(at)) if(++count>8192) invalid();
  const chunks=pngChunks(b), header=chunks[0].data;
  return dimensions(header.readUInt32BE(0),header.readUInt32BE(4),chunks.some(c=>c.type==='acTL'));
}
