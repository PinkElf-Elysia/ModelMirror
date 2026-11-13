import {parseFragment} from 'parse5';
import {fail} from './contracts.mjs';

// Public listing text only. Never include prompt sources or execute author HTML.
export function descriptionText(html) {
  const root = parseFragment(html);
  const text = node => ['script','style','template'].includes(node.tagName) ? '' :
    node.nodeName === '#text' ? node.value : (node.childNodes || []).map(text).join(' ');
  return text(root).replace(/\s+/gu, ' ').trim().slice(0, 600);
}
export function libraryEntry(workId, version, snapshot) {
  const {document, assets} = snapshot;
  const cover = assets.find(asset => asset.alias === document.settings.media.cover && asset.mediaType.startsWith('image/'));
  return {
    id: workId, versionId: version.id, versionNumber: version.number, publishedAt: version.createdAt,
    name: document.metadata.name, description: descriptionText(document.sources.description),
    tags: document.metadata.tags, language: document.metadata.language,
    cover: cover ? `/rpg-app/api/authoring/library/${workId}/${version.id}/cover` : null,
  };
}
export function isLoopbackOrigin(value) {
  try {
    const url = new URL(value);
    return url.origin === value && url.protocol === 'http:' && ['127.0.0.1','localhost'].includes(url.hostname);
  } catch { return false; }
}

export async function publishedWork(store,workId,versionId) {
  if(typeof versionId!=='string'||!/^version-[a-f0-9]{64}$/.test(versionId))fail('AUTHORING_VERSION_ID_INVALID');
  const work=await store.read(workId);
  const version=work.versions.find(item=>item.id===versionId);
  if(!version)fail('AUTHORING_VERSION_NOT_FOUND');
  const snapshot=await store.exportConfiguration(workId,versionId);
  return {id:work.id,revision:version.sourceRevision,draft:snapshot.document,assets:snapshot.assets,
    versions:[version],publishedVersionId:version.id,pending:null};
}
export async function listPublished(store) {
  const entries=[];
  for(const item of await store.list()) {
    if(!item.versions)continue;
    const work=await store.read(item.id),version=work.versions.at(-1);
    if(version)entries.push(libraryEntry(work.id,version,await store.exportConfiguration(work.id,version.id)));
  }
  return entries.sort((a,b)=>b.publishedAt.localeCompare(a.publishedAt)||a.id.localeCompare(b.id));
}
