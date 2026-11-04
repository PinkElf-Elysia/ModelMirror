import {LIMITS, canonical, fail, isHash, isId, keys, validateManifest} from './contracts.mjs';

export const GALLERY_FORMAT = 'modelmirror.rpg.authoring-gallery/1';
export const GALLERY_LIMITS = Object.freeze({groups: 20, groupNameCharacters: 6, imageNameCharacters: 120,
  uploadBatch: 20, images: LIMITS.assets, imageBytes: LIMITS.imageBytes, totalBytes: LIMITS.assetBytes});
export const galleryIdentity = (kind, hash) => {
  if (!['image', 'group'].includes(kind) || !isHash(hash)) fail('AUTHORING_GALLERY_ID_INVALID');
  return kind + '-' + hash;
};
export function galleryId(value, kind) {
  if (!['image', 'group'].includes(kind) || typeof value !== 'string' || !new RegExp('^' + kind + '-[a-f0-9]{64}$').test(value)) fail('AUTHORING_GALLERY_ID_INVALID');
  return value;
}
const unique = values => { if (new Set(values).size !== values.length) fail('AUTHORING_GALLERY_DUPLICATE'); };
const normalizedName = value => value.normalize('NFKC').toLowerCase();
export function galleryName(value, maximum, path) {
  if (typeof value !== 'string' || !value.isWellFormed() || !value.trim() || value !== value.trim() || /[\u0000-\u001f\u007f]/.test(value) || [...value].length > maximum) fail('AUTHORING_GALLERY_NAME_INVALID', path);
  return value;
}
export function galleryGroup(value, groups) {
  if (value !== null && !groups.some(group => group.id === value)) fail('AUTHORING_GALLERY_GROUP_NOT_FOUND');
}
export const blankGallery = () => ({revision: 0, groups: [], images: [], operations: []});
export function validateGallery(value) {
  canonical(value); keys(value, ['revision', 'groups', 'images', 'operations']);
  if (!Number.isSafeInteger(value.revision) || value.revision < 0) fail('AUTHORING_GALLERY_CORRUPT');
  if (!Array.isArray(value.groups) || value.groups.length > GALLERY_LIMITS.groups) fail('AUTHORING_GALLERY_GROUP_CAPACITY');
  for (const group of value.groups) {
    keys(group, ['id', 'name']); galleryId(group.id, 'group'); galleryName(group.name, GALLERY_LIMITS.groupNameCharacters, '/groups/name');
    if (normalizedName(group.name) === '默认图库') fail('AUTHORING_GALLERY_NAME_RESERVED');
  }
  unique(value.groups.map(group => group.id)); unique(value.groups.map(group => normalizedName(group.name)));
  if (!Array.isArray(value.images) || value.images.length > GALLERY_LIMITS.images) fail('AUTHORING_GALLERY_IMAGE_CAPACITY');
  for (const image of value.images) {
    keys(image, ['id', 'name', 'groupId', 'asset']); galleryId(image.id, 'image');
    galleryName(image.name, GALLERY_LIMITS.imageNameCharacters, '/images/name'); galleryGroup(image.groupId, value.groups);
    if (image.asset?.alias !== image.id || typeof image.asset?.mediaType !== 'string' || !image.asset.mediaType.startsWith('image/')) fail('AUTHORING_GALLERY_ASSET_INVALID');
  }
  unique(value.images.map(image => image.id)); validateManifest(value.images.map(image => image.asset));
  if (!Array.isArray(value.operations) || value.operations.length > LIMITS.operations || value.operations.length !== value.revision) fail('AUTHORING_GALLERY_CORRUPT');
  for (const [index, receipt] of value.operations.entries()) {
    keys(receipt, ['operationId', 'requestHash', 'kind', 'scope', 'revision', 'at', 'result']);
    if (!isId(receipt.operationId) || !isHash(receipt.requestHash) || receipt.scope !== 'gallery' || receipt.revision !== index + 1 || typeof receipt.kind !== 'string' || typeof receipt.at !== 'string') fail('AUTHORING_GALLERY_CORRUPT');
  }
  unique(value.operations.map(receipt => receipt.operationId));
  return value;
}
