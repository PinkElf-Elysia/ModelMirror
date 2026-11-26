// Container inspection only. Never decode, open a URL, or pass media to the browser.
const tag = (b, at) => b.toString('latin1', at, at + 4);
const check = (condition, reason) => { if (!condition) throw new Error(reason); };

function pcmWave(b) {
  check(b.length >= 12 && tag(b, 0) === 'RIFF' && tag(b, 8) === 'WAVE' &&
    b.readUInt32LE(4) === b.length - 8, 'RIFF/WAVE 文件头或长度无效');
  let align = 0, samples = 0, chunks = 0;
  for (let at = 12; at < b.length;) {
    check(++chunks <= 8192 && at + 8 <= b.length, 'WAV 块数量或长度无效');
    const kind = tag(b, at), size = b.readUInt32LE(at + 4), data = at + 8;
    at = data + size + size % 2;
    check(at <= b.length, 'WAV 块被截断');
    if (kind === 'fmt ') {
      check(!align && size >= 16 && b.readUInt16LE(data) === 1, '仅支持标准 PCM WAVE');
      const channels = b.readUInt16LE(data + 2), rate = b.readUInt32LE(data + 4);
      const bits = b.readUInt16LE(data + 14);
      align = b.readUInt16LE(data + 12);
      check(channels && rate && [8, 16, 24, 32].includes(bits) && align === channels * bits / 8 &&
        b.readUInt32LE(data + 8) === rate * align, 'PCM 采样参数不一致');
    } else if (kind === 'data') {
      check(align && size > 0 && size % align === 0, 'PCM 采样块无效');
      samples += size / align;
    }
  }
  check(samples > 0, '缺少 PCM 采样数据');
}

const ratesV1 = [0,32,40,48,56,64,80,96,112,128,160,192,224,256,320];
const ratesV2 = [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160];
function layerThree(b) {
  let at = 0, end = b.length, frames = 0, firstRate;
  if (b.toString('latin1', 0, 3) === 'ID3') {
    check(end >= 10 && [2,3,4].includes(b[3]) && b[4] !== 255, 'ID3 文件头无效');
    let size = 0;
    for (const n of b.subarray(6,10)) { check(n < 128, 'ID3 长度无效'); size = size * 128 + n; }
    at = 10 + size;
    if (b[3] === 4 && (b[5] & 16)) {
      check(at + 10 <= end && b.toString('latin1', at, at + 3) === '3DI' &&
        b.subarray(at + 3, at + 10).equals(b.subarray(3, 10)), 'ID3 尾标无效');
      at += 10;
    }
  }
  if (end >= 128 && b.toString('latin1', end - 128, end - 125) === 'TAG') end -= 128;
  while (at < end) {
    check(++frames <= 131072 && at + 4 <= end, 'MP3 帧数量或长度无效');
    const header = b[at + 1], version = (header >> 3) & 3;
    const bitrateIndex = b[at + 2] >> 4, rateIndex = (b[at + 2] >> 2) & 3;
    check(b[at] === 255 && (header & 224) === 224 && version !== 1 && ((header >> 1) & 3) === 1 &&
      bitrateIndex > 0 && bitrateIndex < 15 && rateIndex < 3 && (b[at + 3] & 3) !== 2, '无法核验 MPEG Layer III 帧');
    const rate = [44100,48000,32000][rateIndex] / (version === 3 ? 1 : version === 2 ? 2 : 4);
    const kbps = (version === 3 ? ratesV1 : ratesV2)[bitrateIndex];
    const length = Math.floor((version === 3 ? 144000 : 72000) * kbps / rate) + ((b[at + 2] >> 1) & 1);
    firstRate ??= rate;
    at += length;
    check(at <= end && rate === firstRate, 'MP3 帧被截断或采样率变化');
  }
  check(frames >= 2, '需要至少两个连续完整的 MP3 帧');
}

function localMp4(b) {
  let boxCount = 0, offsetsChecked = 0, samplesChecked = 0;
  function boxes(start, end) {
    const rows = [];
    for (let at = start; at < end;) {
      check(++boxCount <= 16384 && at + 8 <= end, 'MP4 容器层级或块数量超限');
      let size = b.readUInt32BE(at), data = at + 8;
      const type = tag(b, at + 4);
      if (size === 1) {
        check(data + 8 <= end && b.readUInt32BE(data) === 0, 'MP4 扩展块长度无效');
        size = b.readUInt32BE(data + 4); data += 8;
      } else if (size === 0) size = end - at;
      check(size >= data - at && at + size <= end, 'MP4 块被截断');
      at += size; rows.push({type, data, end:at});
    }
    return rows;
  }
  const children = row => boxes(row.data, row.end);
  function one(rows, type, min = 0) {
    const matches = rows.filter(row => row.type === type);
    check(matches.length === 1 && matches[0].end - matches[0].data >= min, '缺少有效的 MP4 ' + type + ' 块');
    return matches[0];
  }
  const root = boxes(0,b.length), brand = one(root,'ftyp',8), movie = children(one(root,'moov'));
  const brands = new Set(['isom','iso2','iso3','iso4','iso5','iso6','mp41','mp42','avc1','M4V ']);
  let compatible = brands.has(tag(b,brand.data));
  check((brand.end-brand.data)%4 === 0, 'MP4 品牌列表无效');
  for (let at = brand.data+8; !compatible && at < brand.end; at += 4) compatible = brands.has(tag(b,at));
  check(compatible && !root.some(r=>r.type==='moof') && !movie.some(r=>r.type==='mvex'), '仅检查常规非分片 MP4');
  const media = root.filter(r=>r.type==='mdat' && r.data < r.end);
  check(media.length > 0, '缺少本地 MP4 媒体数据');
  let video = false;
  for (const track of movie.filter(r=>r.type==='trak')) {
    const mdia = children(one(children(track),'mdia')), hdlr = one(mdia,'hdlr',24);
    const minf = children(one(mdia,'minf')), ref = one(children(one(minf,'dinf')),'dref',8);
    const refs = boxes(ref.data + 8,ref.end);
    check(refs.length > 0 && refs.length === b.readUInt32BE(ref.data+4) &&
      refs.every(r=>r.type==='url ' && r.end-r.data===4 && b.readUInt32BE(r.data)===1), '含外部或未识别的媒体数据引用');
    const table = children(one(minf,'stbl')), desc = one(table,'stsd',8);
    const samples = boxes(desc.data+8,desc.end);
    check(samples.length > 0 && samples.length === b.readUInt32BE(desc.data+4) &&
      samples.every(s=>s.end-s.data>=8 && b.readUInt16BE(s.data+6)>0 && b.readUInt16BE(s.data+6)<=refs.length), 'MP4 样本引用无效');
    const offsets = table.filter(r=>r.type==='stco'||r.type==='co64');
    check(offsets.length === 1, 'MP4 块偏移表无效');
    const off = offsets[0], step = off.type==='stco'?4:8;
    check(off.end-off.data>=8, 'MP4 块偏移表被截断');
    const count = b.readUInt32BE(off.data+4);
    offsetsChecked += count;
    check(count > 0 && offsetsChecked <= 262144 && off.end-off.data===8+count*step, 'MP4 块偏移数量无效');
    const sizes=one(table,'stsz',12), fixed=b.readUInt32BE(sizes.data+4), sampleCount=b.readUInt32BE(sizes.data+8);
    samplesChecked+=sampleCount;
    check(sampleCount>0 && samplesChecked<=1048576 &&
      sizes.end-sizes.data===12+(fixed?0:4*sampleCount), 'MP4 样本大小表无效或超过检查上限');
    const layout=one(table,'stsc',8), runs=b.readUInt32BE(layout.data+4);
    check(runs>0 && runs<=count && layout.end-layout.data===8+runs*12, 'MP4 样本分组无效');
    let run=0, sample=0, perChunk=0, nextChunk=1;
    for (let chunk=1; chunk<=count; chunk++) {
      if (chunk===nextChunk) {
        const start=layout.data+8+12*run;
        check(b.readUInt32BE(start)===chunk, 'MP4 样本分组起点无效');
        perChunk=b.readUInt32BE(start+4);
        const descId=b.readUInt32BE(start+8);
        check(perChunk>0 && descId>0 && descId<=samples.length,'MP4 样本描述索引无效');
        run++;
        nextChunk=run===runs ? count+1 : b.readUInt32BE(start+12);
        check(nextChunk>chunk && (run===runs || nextChunk<=count),'MP4 样本分组顺序无效');
      }
      check(sample+perChunk<=sampleCount,'MP4 样本数量不一致');
      let bytes=perChunk*fixed;
      if (!fixed) for (let i=0;i<perChunk;i++) bytes+=b.readUInt32BE(sizes.data+12+4*(sample+i));
      sample+=perChunk;
      const at=off.data+8+(chunk-1)*step;
      check(step===4 || b.readUInt32BE(at)===0, 'MP4 偏移超出本包');
      const pos = b.readUInt32BE(at+step-4);
      let lo=0, hi=media.length;
      while (lo<hi) {const mid=(lo+hi)>>>1;if(media[mid].end<=pos)lo=mid+1;else hi=mid;}
      check(lo<media.length && media[lo].data<=pos && bytes<=media[lo].end-pos, 'MP4 样本范围未落在本地媒体内');
    }
    check(sample===sampleCount, 'MP4 样本数量不一致');
    if (tag(b,hdlr.data+8)==='vide') {
      check(samples.every(s=>s.end-s.data>=78 && b.readUInt16BE(s.data+24)>0 && b.readUInt16BE(s.data+26)>0), 'MP4 视频样本说明无效');
      video = true;
    }
  }
  check(video, '未发现本地视频轨');
}

const formats = new Map([
  ['wav', {validate:pcmWave, kind:'audio', label:'WAV 音频（PCM）'}],
  ['mp3', {validate:layerThree, kind:'audio', label:'MP3 音频（Layer III）'}],
  ['mp4', {validate:localMp4, kind:'video', label:'MP4 视频'}],
]);
// Check a recognizable signature even when the archive filename was mislabeled.
function signature(bytes) {
  if (tag(bytes,0)==='RIFF' && tag(bytes,8)==='WAVE') return 'wav';
  if (tag(bytes,4)==='ftyp') return 'mp4';
  if (bytes.toString('latin1',0,3)==='ID3' ||
      (bytes[0]===255 && (bytes[1]&224)===224 && ((bytes[1]>>1)&3)===1)) return 'mp3';
  return null;
}
export function inspectMedia(bytes, ext) {
  const detected=signature(bytes);
  const format = formats.get(detected ?? ext);
  if (!format) return null;
  try {
    format.validate(bytes);
    return {kind:format.kind, format:detected ?? ext, label:format.label, verified:true};
  } catch (error) {
    return {kind:format.kind, format:detected ?? ext, label:format.label, verified:false, diagnostic:error.message};
  }
}
