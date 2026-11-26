import {fromBufferPromise} from 'yauzl';
import {crc32} from 'node:zlib';
import {fail as problem} from '../authoring/contracts.mjs';

const MiB = 1024 * 1024;
export const charxLimits = Object.freeze({archive:32*MiB, file:16*MiB, total:64*MiB, entries:256, pixels:40_000_000});
const reject = (reason, path='') => problem('CHARACTER_CHARX_INVALID', path.slice(0,512), {diagnostic:reason});

export function safeArchivePath(name) {
  return name.length <= 512 && name.split('/').every(part =>
    part.length > 0 && part.length <= 200 && part !== '.' && part !== '..' &&
    !/[. ]$/.test(part) && !/[<>:"|?*\\%\x00-\x1f\x7f]/u.test(part) &&
    !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(part));
}

function registerPath(paths, name, directory) {
  const parts = name.split('/');
  for (let i=1; i<=parts.length; i++) {
    const path = parts.slice(0,i).join('/');
    const key = path.normalize('NFC').toLowerCase();
    const last = i === parts.length;
    const folder = !last || directory;
    const old = paths.get(key);
    if (old && (old.path !== path || old.folder !== folder || (last && old.explicit))) reject('路径重复或相互冲突', name);
    paths.set(key, {path, folder, explicit: last || old?.explicit});
  }
}

export async function readCharxArchive(bytes) {
  if (bytes.length > charxLimits.archive || bytes.length < 22) reject('压缩包体积无效');
  // A ZIP comment is part of the original. Limit the search to its 16-bit length.
  let end = bytes.length-22;
  const first=Math.max(0,end-65535);
  while(end>=first && (bytes.readUInt32LE(end)!==0x06054b50 || end+22+bytes.readUInt16LE(end+20)!==bytes.length)) end--;
  if (end<first || bytes.readUInt32LE(end+4) || bytes.readUInt16LE(end+8)!==bytes.readUInt16LE(end+10)) reject('不支持此 ZIP 封装或文件不完整');
  const central = bytes.readUInt32LE(end+16);
  if (central+bytes.readUInt32LE(end+12)!==end || bytes.readUInt16LE(end+10)>charxLimits.entries) reject('目录长度或条目数超限');
  let zip;
  try {
    zip = await fromBufferPromise(bytes, {lazyEntries:true, strictFileNames:true, validateEntrySizes:true, autoClose:false});
    const files = new Map(), paths = new Map(), spans = [];
    let total = 0;
    for await (const entry of zip.eachEntry()) {
      const name = entry.fileName, directory = name.endsWith('/');
      const path = directory ? name.slice(0,-1) : name;
      if (!safeArchivePath(path) || !Buffer.from(name,'utf8').equals(entry.fileNameRaw)) reject('包内路径无效', name);
      registerPath(paths,path,directory);
      const type = (entry.externalFileAttributes >>> 16) & 0xf000;
      if (type && type!==(directory?0x4000:0x8000) || (entry.externalFileAttributes&8) || (!directory && (entry.externalFileAttributes&16))) reject('链接或特殊条目',name);
      if (![0,8].includes(entry.compressionMethod) || (entry.generalPurposeBitFlag & ~0x80e) || entry.versionNeededToExtract>20 ||
          entry.extraFields.some(field=>field.id===1)) reject('压缩方式、加密或 ZIP 标志不支持',name);
      if (entry.uncompressedSize>charxLimits.file || total+entry.uncompressedSize>charxLimits.total ||
          (directory && entry.uncompressedSize!==0)) reject('解压大小超限或目录带有正文',name);
      const local = await zip.readLocalFileHeaderPromise(entry);
      const descriptor=Boolean(entry.generalPurposeBitFlag & 8);
      const values=['crc32','compressedSize','uncompressedSize'];
      if (!local.fileName.equals(entry.fileNameRaw) ||
          ['generalPurposeBitFlag','compressionMethod','versionNeededToExtract'].some(k=>local[k]!==entry[k]) ||
          values.some(k=>local[k]!==entry[k] && !(descriptor && local[k]===0))) reject('本地文件头与中央目录不一致',name);
      const start=entry.relativeOffsetOfLocalHeader;
      let stop=start+30+local.fileNameLength+local.extraFieldLength+entry.compressedSize;
      if(descriptor) {
        if(stop+12>central) reject('数据描述符不完整',name);
        if(bytes.readUInt32LE(stop)===0x08074b50) stop+=4;
        if(stop+12>central || values.some((k,i)=>bytes.readUInt32LE(stop+4*i)!==entry[k])) reject('数据描述符与目录不一致',name);
        stop+=12;
      }
      spans.push([start,stop]);
      const chunks=[];
      let size=0, checksum=0;
      for await (const chunk of await zip.openReadStreamPromise(entry)) {
        size+=chunk.length;
        if(size>entry.uncompressedSize || total+size>charxLimits.total) reject('实际解压大小超限',name);
        checksum=crc32(chunk,checksum);
        chunks.push(chunk);
      }
      if(size!==entry.uncompressedSize || checksum!==entry.crc32) reject('文件长度或 CRC 校验失败',name);
      total+=size;
      if (!directory) files.set(name,Buffer.concat(chunks,size));
    }
    let next=0;
    for (const [start,stop] of spans.sort((a,b)=>a[0]-b[0])) {
      if(start!==next || stop>central) reject('文件边界重叠或含未登记内容');
      next=stop;
    }
    if(next!==central || zip.readEntryCursor!==end) reject('目录边界不一致');
    return files;
  } catch(e) {
    if(e.code==='CHARACTER_CHARX_INVALID') throw e;
    reject('无法完整读取 ZIP 目录或条目');
  } finally {zip?.close();}
}
