import test from 'node:test';
import assert from 'node:assert/strict';
import {zipSync} from 'fflate';
import {blankCard} from './card.mjs';
import {inspectMedia} from './charx-media.mjs';
import {inspectCharx} from './charx.mjs';

const word = n => { const b=Buffer.alloc(4); b.writeUInt32BE(n); return b; };
const box = (type,...fields) => {
  const data=Buffer.concat(fields);
  return Buffer.concat([word(8+data.length),Buffer.from(type),data]);
};
const full = Buffer.alloc(4);
function mp4({handler='vide',local=true,offset=28,sampleSize=4}={}) {
  const ftyp=box('ftyp',Buffer.from('isom'),word(0),Buffer.from('mp42'));
  const data=box('mdat',Buffer.alloc(4));
  const dref=box('dref',full,word(1),box('url ',word(local?1:0)));
  const frame=Buffer.alloc(78);frame.writeUInt16BE(1,6);frame.writeUInt16BE(32,24);frame.writeUInt16BE(24,26);
  const stbl=box('stbl',box('stsd',full,word(1),box('mp4v',frame)),
    box('stsc',full,word(1),word(1),word(1),word(1)),box('stsz',full,word(sampleSize),word(1)),
    box('stco',full,word(1),word(offset)));
  const hdlr=box('hdlr',full,word(0),Buffer.from(handler),Buffer.alloc(12));
  const mdia=box('mdia',hdlr,box('minf',box('dinf',dref),stbl));
  return Buffer.concat([ftyp,data,box('moov',box('trak',mdia))]);
}
function wave() {
  const b=Buffer.from('524946462600000057415645666d74201000000001000100401f0000803e00000200100064617461020000000000','hex');
  return b;
}
const frame=Buffer.concat([Buffer.from('fffb9000','hex'),Buffer.alloc(413)]);
const mp3=Buffer.concat([frame,frame]);
test('checks PCM chunks, agreement of sample parameters, truncation and padded chunks',()=>{
  assert.equal(inspectMedia(wave(),'wav').verified,true);
  const withPad=Buffer.concat([wave(),Buffer.from('4a554e4b010000000000','hex')]);
  withPad.writeUInt32LE(withPad.length-8,4);
  assert.equal(inspectMedia(withPad,'wav').verified,true);
  for(const [at,value] of [[20,3],[28,0],[32,1],[40,4]]) {
    const b=wave();b[at]=value===0?1:value;
    assert.equal(inspectMedia(b,'wav').verified,false);
  }
  assert.equal(inspectMedia(wave().subarray(0,-1),'wav').verified,false);
});
test('checks contiguous Layer III frames and bounded ID3 fields',()=>{
  const id3=Buffer.from('49443304000000000001ff','hex');
  assert.equal(inspectMedia(Buffer.concat([id3,mp3]),'mp3').verified,true);
  for(const bytes of [mp3.subarray(0,-1),Buffer.concat([mp3,Buffer.from([0])]),frame,
    Buffer.concat([Buffer.from('4944330400007f7f7f7f','hex'),mp3]),Buffer.alloc(5)]) {
    assert.equal(inspectMedia(bytes,'mp3').verified,false);
  }
  const layerTwo=Buffer.from(mp3);layerTwo[1]=0xfd;
  assert.equal(inspectMedia(layerTwo,'mp3').verified,false);
});
test('accepts local video tracks, rejects external, absent, fragmented or out-of-range samples',()=>{
  assert.equal(inspectMedia(mp4(),'mp4').verified,true);
  for(const bytes of [mp4({handler:'soun'}),mp4({local:false}),mp4({offset:10}),mp4({offset:31,sampleSize:2}),
    mp4().subarray(0,-1),Buffer.concat([mp4(),box('moof')]),Buffer.alloc(20)]){
    assert.equal(inspectMedia(bytes,'mp4').verified,false);
  }
});
test('media inspection retains all uses, per-file diagnostics, original-only eligibility and case-sensitive references',async()=>{
  const card=blankCard();Object.assign(card,{spec:'chara_card_v3',spec_version:'3.0'});
  const paths=['one/bell.wav','two/bell.wav','quiet.mp3','clip.mp4'];
  const assets=paths.map(path=>({type:'other',name:'灯',uri:'embeded://'+path,ext:path.split('.').pop()}));
  assets.push({...assets[0],name:'共享提示'}, {...assets[3],ext:'wav'}, {...assets[0],uri:'embeded://One/bell.wav'});
  Object.assign(card.data,{group_only_greetings:[],assets});
  const bytes=Buffer.from(zipSync({'card.json':Buffer.from(JSON.stringify(card)),
    [paths[0]]:wave(),[paths[1]]:wave(),[paths[2]]:mp3,[paths[3]]:mp4(),'other.mp3':Buffer.alloc(20),'mislabeled.png':wave()}));
  const report=await inspectCharx(bytes), row=path=>report.members.find(r=>r.path===path);
  assert.equal(report.classification,'original-only');
  assert.equal(row(paths[0]).uses.length,2);assert.match(row(paths[0]).status,/共 2 处引用/);
  assert.equal(row(paths[1]).media.verified,true);assert.equal(row(paths[2]).media.format,'mp3');
  assert.match(row(paths[3]).status,/声明格式与文件不符/);
  assert.match(row('other.mp3').status,/未核验.*未引用/);
  assert.match(row('One/bell.wav').status,/找不到/);
  assert.match(row('mislabeled.png').status,/WAV.*后缀与媒体容器不符/);
});

test('structurally valid media packages have a distinct importable classification',async()=>{
  const card=blankCard();Object.assign(card,{spec:'chara_card_v3',spec_version:'3.0'});
  const files={'bell.wav':wave(),'quiet.mp3':mp3,'clip.mp4':mp4()};
  Object.assign(card.data,{group_only_greetings:[],assets:Object.keys(files).map(path=>({
    type:'other',name:path,uri:'embeded://'+path,ext:path.split('.')[1],
  }))});
  const report=await inspectCharx(Buffer.from(zipSync({'card.json':Buffer.from(JSON.stringify(card)),...files})));
  assert.deepEqual(report.members.filter(r=>r.media?.verified).map(r=>r.media.kind),['audio','audio','video']);
  assert.equal(report.classification,'media');
  assert.deepEqual(report.diagnostics,[]);
});
test('WAV and MP4 stop at chunk-count limits even for small input files',()=>{
  const emptyChunk=Buffer.concat([Buffer.from('JUNK'),Buffer.alloc(4)]);
  const wav=Buffer.concat([wave(),...Array(8193).fill(emptyChunk)]);
  wav.writeUInt32LE(wav.length-8,4);
  assert.match(inspectMedia(wav,'wav').diagnostic,/数量/);
  const manyBoxes=Buffer.concat([mp4(),...Array(16385).fill(box('free'))]);
  assert.match(inspectMedia(manyBoxes,'mp4').diagnostic,/数量/);
});
