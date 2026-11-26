import {inflateSync} from 'node:zlib';
import {chunk,pngChunks} from './png.mjs';

// Bounds include the default image even when it is outside the animation.
export const portraitLimits=Object.freeze({bytes:4*1024*1024,frames:60,pixels:1024*1024,work:12*1024*1024,chunks:1024});
const check=(ok,message='帧结构无效')=>{if(!ok)throw Error(message);};
export function inspectAnimatedPortrait(bytes) {
  const l=portraitLimits;
  check(bytes.length<=l.bytes,'文件超过 4 MiB');
  const parts=pngChunks(bytes);
  check(parts.length<=l.chunks,'数据块过多');
  const header=parts[0].data,w=header.readUInt32BE(0),h=header.readUInt32BE(4);
  check(w*h<=l.pixels,'画布超过 1048576 像素');
  const channels=({2:3,6:4})[header[9]];
  check(header[8]===8 && channels && header[12]===0,'当前支持非隔行 8 位 RGB 或 RGBA');
  let frames=0,plays=0,sequence=0,controls=0,seenImage=false,endedImage=false;
  const posterData=[],streams=[],posterChunks=[],playChunks=[],once=new Set();
  let current=null;
  for(const {type,data} of parts){
    if(seenImage && type!=='IDAT')endedImage=true;
    switch(type){
      case 'acTL':
        check(!frames && !seenImage && data.length===8);
        frames=data.readUInt32BE(0);plays=data.readUInt32BE(4);
        check(frames>0 && frames<=l.frames,'帧数须为 1 至 60');
        check((frames+1)*w*h<=l.work,'累计画布像素超过解码上限');
        break;
      case 'fcTL': {
        check(frames && data.length===26 && data.readUInt32BE(0)===sequence++ && ++controls<=frames);
        const fw=data.readUInt32BE(4),fh=data.readUInt32BE(8);
        check(fw && fh && data.readUInt32BE(12)+fw<=w && data.readUInt32BE(16)+fh<=h);
        check(data[24]<=2 && data[25]<=1);
        if(!seenImage)check(!current && fw===w && fh===h);
        current={width:fw,height:fh,parts:seenImage?[]:posterData};
        if(seenImage)streams.push(current);
        break;
      }
      case 'IDAT':
        check(frames && !endedImage);
        seenImage=true;posterData.push(data);break;
      case 'fdAT':
        check(seenImage && current && current.parts!==posterData && data.length>=4 && data.readUInt32BE(0)===sequence++);
        current.parts.push(data.subarray(4));break;
      case 'IHDR':case 'IEND':break;
      case 'tRNS':case 'gAMA':case 'sRGB':
        check(!seenImage && !once.has(type));
        once.add(type);
        check(type==='tRNS'?channels===3 && data.length===6:type==='gAMA'?data.length===4 && data.readUInt32BE(0)>0:data.length===1 && data[0]<=3);
        break;
      default:
        // Keep original ancillary metadata in the archive, outside the decoder.
        check(type[0]===type[0].toLowerCase(),'不支持的关键图像块');continue;
    }
    const encoded=chunk(type,data);
    playChunks.push(encoded);
    if(!['acTL','fcTL','fdAT'].includes(type))posterChunks.push(encoded);
  }
  check(frames && frames===controls && seenImage);
  for(const s of [{width:w,height:h,parts:posterData},...streams]){
    const stride=1+s.width*channels,expected=stride*s.height;
    const compressed=Buffer.concat(s.parts);
    check(compressed.length);
    let decoded;
    try{decoded=inflateSync(compressed,{maxOutputLength:expected,info:true});}
    catch{throw Error('像素数据损坏或解码超过上限');}
    check(decoded.buffer.length===expected && decoded.engine.bytesWritten===compressed.length);
    for(let at=0;at<expected;at+=stride)check(decoded.buffer[at]<=4);
  }
  const sig=bytes.subarray(0,8);
  return {frames,plays,poster:Buffer.concat([sig,...posterChunks]),playback:Buffer.concat([sig,...playChunks])};
}
