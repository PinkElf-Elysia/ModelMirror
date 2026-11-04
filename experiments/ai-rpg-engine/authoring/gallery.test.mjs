import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, mkdtemp, readFile, writeFile, readdir, unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {AuthoringStore} from './store.mjs';
import {startAuthoringServer} from './server.mjs';
import {blankDocument, canonical, sha256, LIMITS} from './contracts.mjs';
import {blankGallery, galleryIdentity, validateGallery} from './gallery.mjs';

const parent = fileURLToPath(new URL('../.rpg04-work/authoring-b2c/', import.meta.url));
await mkdir(parent, {recursive: true});
const runRoot = await mkdtemp(join(parent, 'tests-'));
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=';
const secondPng = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
const assetHash = sha256(Buffer.from(png, 'base64'));
const image = (name = '蓝图', groupId = null, base64 = png) => ({name, groupId, fileName: '蓝图.png', mediaType: 'image/png', base64});
const group = (store, revision, name = '复刻核验', operationId = 'group') => store.createGalleryGroup({operationId, expectedRevision: revision, name});
const upload = (store, revision, images = [image()], operationId = 'upload') => store.uploadGalleryImages({operationId, expectedRevision: revision, images});
const rejected = (action, code) => assert.rejects(async () => action(), error => { assert.equal(error.code, code); return true; });
const document = () => { const value = blankDocument(); value.metadata.name = '图库独立样例'; value.metadata.audience = 'male'; value.sources.system = '作者原文\r\n\r\n  不改写 🐟'; return value; };
const create = store => store.create({operationId: 'work', expectedRevision: null, document: document()});
async function fixture(t, options = {}) {
  const directory = await mkdtemp(join(runRoot, 'store-'));
  const store = await AuthoringStore.open(directory, options); t.after(() => store.close());
  return {store, directory};
}
async function hostFixture(t, options = {}) {
  const directory = await mkdtemp(join(runRoot, 'http-'));
  const host = await startAuthoringServer({directory, ...options}); t.after(() => host.close());
  const request = async (path, body, headers = {}) => {
    const response = await fetch(host.origin + '/rpg-app/api/authoring' + path, body === undefined ? {headers} : {
      method: 'POST', headers: {'Content-Type': 'application/json', Origin: host.origin, 'X-Authoring-Request': '1', ...headers}, body: JSON.stringify(body),
    });
    return {status: response.status, value: await response.json()};
  };
  return {directory, host, request};
}

test('旧目录读取图库不迁移作品或创建图库；原格式和作者源码保持原样', async t => {
  const {store, directory} = await fixture(t), made = await create(store);
  const workPath = join(directory, 'works', made.workId + '.json'), before = await readFile(workPath);
  const empty = await store.readGallery();
  assert.equal(empty.revision, 0); assert.deepEqual(empty.images, []); assert.deepEqual(empty.groups, []);
  assert.equal((await readdir(directory)).includes('gallery.json'), false);
  await group(store, 0); await upload(store, 1);
  assert.deepEqual(await readFile(workPath), before);
  assert.deepEqual((await store.read(made.workId)).draft, document());
  assert.equal((await store.list()).length, 1);
});

test('分组、批量上传、改名与移动持久化；名字和分组不改变图片身份、字节或文件名', async t => {
  const {store, directory} = await fixture(t);
  const folder = (await group(store, 0)).result.groupId;
  const uploaded = await upload(store, 1, [image(), image('第二张', folder, secondPng)]);
  const [firstId, secondId] = uploaded.result.imageIds, before = await store.readGallery();
  await store.editGalleryImage({operationId: 'rename', expectedRevision: 2, imageId: firstId, name: '新的显示名', groupId: folder});
  await store.moveGalleryImages({operationId: 'move', expectedRevision: 3, imageIds: [firstId, secondId], groupId: null});
  const after = await store.readGallery();
  assert.deepEqual(after.images.map(item => item.groupId), [null, null]);
  assert.equal(after.images[0].name, '新的显示名');
  assert.deepEqual(after.images.map(item => item.asset), before.images.map(item => item.asset));
  assert.deepEqual((await store.getGalleryImage(firstId)).bytes, Buffer.from(png, 'base64'));
  assert.equal(Object.hasOwn(after, 'operations'), false);
  await store.close(); const reopened = await AuthoringStore.open(directory); t.after(() => reopened.close());
  assert.deepEqual(await reopened.readGallery(), after);
});

test('各图库操作可用原 ID 核对；重用 ID 改请求被拒绝，恢复不追加操作', async t => {
  const {store, directory} = await fixture(t);
  const g = await group(store, 0), u = await upload(store, 1);
  const id = u.result.imageIds[0];
  const editing = {operationId:'edit', expectedRevision:2, imageId:id, name:'新名', groupId:g.result.groupId};
  await store.editGalleryImage(editing);
  const moving = {operationId:'move', expectedRevision:3, imageIds:[id], groupId:null};
  await store.moveGalleryImages(moving);
  assert.equal((await group(store, 0)).replayed, true);
  assert.equal((await upload(store, 1)).replayed, true);
  assert.equal((await store.editGalleryImage(editing)).replayed, true);
  assert.equal((await store.moveGalleryImages(moving)).replayed, true);
  await rejected(() => group(store, 0, '另一组'), 'AUTHORING_OPERATION_COLLISION');
  await rejected(() => upload(store, 1, [image('同 ID 改名')]), 'AUTHORING_OPERATION_COLLISION');
  await rejected(() => store.editGalleryImage({...editing,name:'不同'}), 'AUTHORING_OPERATION_COLLISION');
  await rejected(() => store.moveGalleryImages({...moving,groupId:g.result.groupId}), 'AUTHORING_OPERATION_COLLISION');
  assert.equal((await store.recoverGallery('edit')).status, 'committed');
  assert.equal((await store.recoverGallery('never-submitted')).status, 'not_found');
  assert.equal(JSON.parse(await readFile(join(directory,'gallery.json'),'utf8')).data.operations.length, 4);
});

test('图库并发 revision 竞争只提交一份；调用之后改传入对象不改变已冻结请求', async t => {
  const {store} = await fixture(t);
  const a = {operationId:'a',expectedRevision:0,name:'甲'}, b = {operationId:'b',expectedRevision:0,name:'乙'};
  const first = store.createGalleryGroup(a); a.name = '外部变异';
  const results = await Promise.allSettled([first, store.createGalleryGroup(b)]);
  assert.equal(results.filter(entry => entry.status === 'fulfilled').length, 1);
  assert.equal(results.find(entry => entry.status === 'rejected').reason.code, 'AUTHORING_REVISION_CONFLICT');
  const images = [image()]; const uploading = upload(store, 1, images); images[0].name = '外部改名';
  await uploading;
  const current = await store.readGallery(); assert.equal(current.groups[0].name,'甲'); assert.equal(current.images[0].name,'蓝图');
});

test('Unicode 名称、六字符分组上限、重复组名与保留默认组均严格校验', async t => {
  const {store} = await fixture(t);
  await group(store,0,'一二三四五🐟','six');
  await rejected(() => group(store,1,'一二三四五六🐟','seven'),'AUTHORING_GALLERY_NAME_INVALID');
  await group(store,1,'ABC','ascii');
  await rejected(() => group(store,2,'ａｂｃ','normalized'),'AUTHORING_GALLERY_DUPLICATE');
  await rejected(() => group(store,2,'默认图库','default'),'AUTHORING_GALLERY_NAME_RESERVED');
  for (const [i, name] of ['', '  ', ' 名称', '名称\n', '\uD800'].entries()) {
    await rejected(() => group(store,2,name,'bad'+i), i===4?'AUTHORING_UNICODE_INVALID':'AUTHORING_GALLERY_NAME_INVALID');
  }
  await upload(store,2,[image('🐟'.repeat(120))],'long-title');
  await rejected(() => upload(store,3,[image('🐟'.repeat(121))],'over-title'),'AUTHORING_GALLERY_NAME_INVALID');
});

test('无效批次、未知字段、缺失分组、错文件类型和坏 base64 整批不写入', async t => {
  const {store,directory} = await fixture(t); await upload(store,0);
  const before = await store.readGallery(), blobs = await readdir(join(directory,'blobs'));
  const invalid = [
    [[image('valid',null,secondPng),image('')], 'AUTHORING_GALLERY_NAME_INVALID'],
    [[image('valid',null,secondPng), {...image(),base64:'not-base64'}], 'AUTHORING_BASE64_INVALID'],
    [[{...image(),mediaType:'audio/mpeg'}], 'AUTHORING_ASSET_TYPE'],
    [[{...image(),mediaType:'image/svg+xml'}], 'AUTHORING_ASSET_TYPE'],
    [[image('缺组','group-'+ 'a'.repeat(64))], 'AUTHORING_GALLERY_GROUP_NOT_FOUND'],
    [[{...image(),fileName:'../bad.png'}], 'AUTHORING_FILENAME_INVALID'],
    [[{...image(),sourceUrl:'https://not-contacted.invalid'}], 'AUTHORING_UNKNOWN_FIELD'],
    [[{...image(),base64:Buffer.from('<script>bad()</script>').toString('base64')}], 'AUTHORING_ASSET_TYPE'],
  ];
  for (const [index,[images,code]] of invalid.entries()) await rejected(() => upload(store,1,images,'invalid'+index),code);
  await rejected(() => upload(store,1,[],'empty'),'AUTHORING_GALLERY_BATCH_CAPACITY');
  await rejected(() => upload(store,1,Array.from({length:21},()=>image()),'large'),'AUTHORING_GALLERY_BATCH_CAPACITY');
  assert.deepEqual(await store.readGallery(), before); assert.deepEqual(await readdir(join(directory,'blobs')),blobs);
});

test('混合缺失 ID 的批量移动不产生部分更新，未知编辑字段不被吞掉', async t => {
  const {store} = await fixture(t), folder = (await group(store,0)).result.groupId;
  const ids = (await upload(store,1,[image(),image('二')])).result.imageIds;
  const before = await store.readGallery();
  await rejected(() => store.moveGalleryImages({operationId:'mixed',expectedRevision:2,imageIds:[ids[0],'image-'+'f'.repeat(64)],groupId:folder}), 'AUTHORING_GALLERY_IMAGE_NOT_FOUND');
  await rejected(() => store.moveGalleryImages({operationId:'duplicate',expectedRevision:2,imageIds:[ids[0],ids[0]],groupId:folder}), 'AUTHORING_GALLERY_SELECTION_INVALID');
  await rejected(() => store.editGalleryImage({operationId:'unknown',expectedRevision:2,imageId:ids[0],name:'改名',groupId:folder,bytes:'surprise'}),'AUTHORING_UNKNOWN_FIELD');
  assert.deepEqual(await store.readGallery(), before);
});

test('20 个分组及图片数量、每图与全库字节容量有独立校验，不截断或自动腾挪', async t => {
  const {store} = await fixture(t);
  for (let i=0;i<20;i++) await group(store,i,'组'+i,'g'+i);
  await rejected(()=>group(store,20,'超限','g20'),'AUTHORING_GALLERY_GROUP_CAPACITY');
  assert.equal((await store.readGallery()).groups.length,20);
  const data=blankGallery();
  const entry = i => { const id=galleryIdentity('image',sha256(String(i))); return {id,name:'图',groupId:null,asset:{alias:id,fileName:'x.png',mediaType:'image/png',size:Buffer.from(png,'base64').length,sha256:assetHash}}; };
  data.images=Array.from({length:256},(_,i)=>entry(i)); validateGallery(data);
  data.images.push(entry(256)); assert.throws(()=>validateGallery(data),{code:'AUTHORING_GALLERY_IMAGE_CAPACITY'});
  data.images=[entry(1)]; data.images[0].asset.size=LIMITS.imageBytes+1;
  assert.throws(()=>validateGallery(data),{code:'AUTHORING_NUMBER_INVALID'});
  data.images=Array.from({length:13},(_,i)=>{const e=entry(i);e.asset.size=LIMITS.imageBytes;e.asset.sha256=sha256('different'+i);return e;});
  assert.throws(()=>validateGallery(data),{code:'AUTHORING_ASSET_TOTAL_SIZE'});
});

test('图库入作品为快照；改名/移动不污染作品、复制与封存，包在空目录回导自包含', async t => {
  const {store}=await fixture(t), target=await fixture(t), made=await create(store);
  const folder=(await group(store,0)).result.groupId, id=(await upload(store,1)).result.imageIds[0];
  const selection={operationId:'adopt',expectedRevision:0,galleryRevision:2,imageId:id,alias:'cover'};
  await store.adoptGalleryImage(made.workId,selection);
  const doc=document();doc.settings.media.cover='cover';doc.sources.introduction='<img src="asset://cover">';
  await store.save(made.workId,{operationId:'set-cover',expectedRevision:1,document:doc});
  const version=await store.publish(made.workId,{operationId:'seal',expectedRevision:2});
  const pack=await store.exportPackage(made.workId,version.result.versionId), workBefore=await store.read(made.workId);
  await store.editGalleryImage({operationId:'rename',expectedRevision:2,imageId:id,name:'后来的显示名',groupId:folder});
  assert.deepEqual(await store.read(made.workId),workBefore);
  assert.deepEqual(await store.exportPackage(made.workId,version.result.versionId),pack);
  assert.equal((await store.adoptGalleryImage(made.workId,selection)).replayed,true);
  const copy=await store.copy(made.workId,{operationId:'copy',expectedRevision:3,name:'图库副本'});
  assert.deepEqual((await store.exportPackage(copy.workId)).blobs,pack.blobs);
  const inspected=await target.store.inspectImport(pack);
  const imported=await target.store.importPackage({operationId:'import',expectedRevision:null,workId:null,payload:pack,expectedImportHash:inspected.payloadHash});
  assert.deepEqual(await target.store.exportPackage(imported.workId),pack);
  assert.deepEqual((await target.store.readGallery()).images,[]);
  assert.equal(Object.hasOwn(await store.read(made.workId),'budget'),false);
});

test('选用校验作品和图库两份 revision，别名冲突、缺失/损坏资源均不覆盖作品', async t => {
  const {store,directory}=await fixture(t), made=await create(store), id=(await upload(store,0)).result.imageIds[0];
  const select={operationId:'pick',expectedRevision:0,galleryRevision:1,imageId:id,alias:'cover'};
  await group(store,1);
  await rejected(()=>store.adoptGalleryImage(made.workId,select),'AUTHORING_GALLERY_REVISION_CONFLICT');
  await store.adoptGalleryImage(made.workId,{...select,galleryRevision:2});
  await rejected(()=>store.adoptGalleryImage(made.workId,{...select,operationId:'stale-work',galleryRevision:2}),'AUTHORING_REVISION_CONFLICT');
  await rejected(()=>store.adoptGalleryImage(made.workId,{...select,operationId:'alias',expectedRevision:1,galleryRevision:2}),'AUTHORING_ALIAS_CONFLICT');
  const before=await store.read(made.workId), blob=join(directory,'blobs',assetHash);
  await writeFile(blob,Buffer.from(secondPng,'base64'));
  await rejected(()=>store.adoptGalleryImage(made.workId,{...select,operationId:'corrupt',expectedRevision:1,galleryRevision:2,alias:'other'}),'AUTHORING_ASSET_CORRUPT');
  await unlink(blob);
  await rejected(()=>store.getGalleryImage(id),'AUTHORING_ASSET_MISSING');
  assert.deepEqual(await store.read(made.workId),before);
});

test('图库提交前失败保持旧目录，同 ID 可由明确调用重试；不后台重放', async t => {
  let armed=true;
  const {store}=await fixture(t,{fault:(phase)=>{if(phase==='beforeGalleryCommit'&&armed){armed=false;throw new Error('disk fault');}}});
  await rejected(()=>group(store,0),'AUTHORING_WRITE_FAILED');
  assert.equal((await store.readGallery()).revision,0); assert.equal((await store.readGallery()).pending,null);
  assert.equal((await store.recoverGallery('group')).status,'not_found');
  assert.equal((await group(store,0)).revision,1);
});

test('图库提交后未知阻断图库写入和选用；重启只核对原 ID，独立作品仍可保存', async t => {
  let armed=false;
  const {store,directory}=await fixture(t,{fault:phase=>{if(armed&&phase==='afterGalleryCommit')throw new Error('lost reply');}});
  const made=await create(store), id=(await upload(store,0)).result.imageIds[0]; armed=true;
  await rejected(()=>group(store,1),'AUTHORING_RESULT_UNKNOWN');
  assert.equal((await store.readGallery()).pending.operationId,'group');
  await rejected(()=>upload(store,2,[image()],'new'),'AUTHORING_OPERATION_PENDING');
  await rejected(()=>store.adoptGalleryImage(made.workId,{operationId:'pick',expectedRevision:0,galleryRevision:2,imageId:id,alias:'cover'}),'AUTHORING_OPERATION_PENDING');
  await store.save(made.workId,{operationId:'independent-save',expectedRevision:0,document:document()});
  await store.close();const restarted=await AuthoringStore.open(directory);t.after(()=>restarted.close());
  const before=await readFile(join(directory,'gallery.json'));
  await rejected(()=>restarted.recoverGallery('different'),'AUTHORING_OPERATION_PENDING');
  assert.equal((await restarted.recoverGallery('group')).status,'committed');
  assert.deepEqual(await readFile(join(directory,'gallery.json')),before);
  assert.equal((await group(restarted,1)).replayed,true);
  assert.equal((await restarted.readGallery()).revision,2);
});

test('原操作恢复能区分提交前中断；损坏的恢复指针保持阻断', async t => {
  const {store,directory}=await fixture(t);
  const pending={operationId:'interrupted',requestHash:sha256('request'),previousHash:null};
  await writeFile(join(directory,'gallery-pending.json'),canonical(pending));
  assert.equal((await store.recoverGallery('interrupted')).status,'not_committed');
  await group(store,0);
  await writeFile(join(directory,'gallery-pending.json'),canonical({...pending,previousHash:null}));
  await rejected(()=>store.recoverGallery('interrupted'),'AUTHORING_RECOVERY_CONFLICT');
  assert.equal((await store.readGallery()).pending.operationId,'interrupted');
  await rejected(()=>upload(store,1),'AUTHORING_OPERATION_PENDING');
});

test('HTTP 图库读写、原图、选用、冲突和同源检查接线，未开放任意路径或删图接口', async t => {
  const {host,request}=await hostFixture(t);
  const body={operationId:'g',expectedRevision:0,name:'复刻核验'};
  assert.equal((await request('/gallery/groups',body,{Origin:'https://outside.invalid'})).status,403);
  assert.equal((await request('/gallery/groups',body,{'X-Authoring-Request':''})).status,403);
  assert.equal((await request('/gallery',undefined,{'Sec-Fetch-Site':'cross-site'})).status,403);
  assert.equal((await request('/gallery?ignored=1')).status,422);
  const g=(await request('/gallery/groups',body)).value;
  assert.equal(g.revision,1);assert.equal(g.scope,'gallery');
  assert.equal((await request('/gallery/groups',body)).value.replayed,true);
  const uploaded=await request('/gallery/images',{operationId:'u',expectedRevision:1,images:[image()]});
  assert.equal(uploaded.status,200);const id=uploaded.value.result.imageIds[0];
  const edit={operationId:'e',expectedRevision:2,imageId:id,name:'图改名',groupId:g.result.groupId};
  assert.equal((await request('/gallery/images/edit',edit)).value.revision,3);
  assert.equal((await request('/gallery/images/move',{operationId:'m',expectedRevision:3,imageIds:[id],groupId:null})).value.revision,4);
  const state=(await request('/gallery')).value;assert.equal(state.images[0].name,'图改名');assert.equal(state.limits.groups,20);
  const bytes=await fetch(host.origin+'/rpg-app/api/authoring/gallery/images/'+id+'?hash='+assetHash);
  assert.equal(bytes.status,200);assert.equal(bytes.headers.get('content-type'),'image/png');assert.match(bytes.headers.get('content-security-policy'),/sandbox/);
  assert.deepEqual(Buffer.from(await bytes.arrayBuffer()),Buffer.from(png,'base64'));
  assert.equal((await fetch(host.origin+'/rpg-app/api/authoring/gallery/images/'+id+'?hash=changed')).status,409);
  assert.equal((await request('/gallery/images/..%2f..')).status,422);
  assert.equal((await request('/gallery/images/remove',{imageId:id})).status,404);
  const made=(await request('/works',{operationId:'w',expectedRevision:null,document:document()})).value;
  const select={operationId:'pick',expectedRevision:0,galleryRevision:4,imageId:id,alias:'cover'};
  assert.equal((await request('/works/'+made.workId+'/assets/from-gallery',select)).status,200);
  assert.equal((await request('/works/'+made.workId+'/assets/from-gallery',select)).value.replayed,true);
  assert.equal((await request('/gallery/operations/recover',{operationId:'u'})).value.status,'committed');
  assert.equal((await request('/gallery/operations/recover',{operationId:'u',revision:1})).value.error.code,'AUTHORING_UNKNOWN_FIELD');
  assert.equal((await request('/gallery/groups',{...body,operationId:'stale'})).status,409);
});

test('HTTP 未知结果返回图库作用域且不泄露异常信息，恢复后不重复上传', async t => {
  const {directory,request}=await hostFixture(t,{fault:phase=>{if(phase==='afterGalleryCommit')throw new Error('private-path-and-secret');}});
  const body={operationId:'u',expectedRevision:0,images:[image()]};
  const unknown=await request('/gallery/images',body);
  assert.equal(unknown.status,409);assert.equal(unknown.value.error.code,'AUTHORING_RESULT_UNKNOWN');
  assert.deepEqual(unknown.value.error.details,{operationId:'u',scope:'gallery'});
  assert.equal(JSON.stringify(unknown).includes('private-path'),false);
  assert.equal((await request('/gallery/images',body)).value.error.code,'AUTHORING_OPERATION_PENDING');
  assert.equal((await request('/gallery/operations/recover',{operationId:'u'})).value.status,'committed');
  assert.equal((await request('/gallery/images',body)).value.replayed,true);
  assert.equal(JSON.parse(await readFile(join(directory,'gallery.json'),'utf8')).data.images.length,1);
});


test('损坏图库校验失败关闭，不自动修复、清空或改写原文件', async t => {
  const {store,directory}=await fixture(t); await upload(store,0);
  const file=join(directory,'gallery.json'), original=await readFile(file), corrupt=JSON.parse(original);
  corrupt.data.images[0].name='篡改'; await writeFile(file,canonical(corrupt));
  const before=await readFile(file);
  await rejected(()=>store.readGallery(),'AUTHORING_GALLERY_CORRUPT');
  await rejected(()=>group(store,1),'AUTHORING_GALLERY_CORRUPT');
  assert.deepEqual(await readFile(file),before);
  corrupt.hash=sha256(canonical(corrupt.data)); corrupt.data.images[0].asset.mediaType=42;
  corrupt.hash=sha256(canonical(corrupt.data)); await writeFile(file,canonical(corrupt));
  await rejected(()=>store.readGallery(),'AUTHORING_GALLERY_ASSET_INVALID');
});
