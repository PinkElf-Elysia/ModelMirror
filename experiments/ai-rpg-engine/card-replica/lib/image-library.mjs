// Reviewed image data only; no runtime network, guessed aliases or remote fallback.
import library from '../resources/image-library.json' with {type:'json'};
export function imageMap(card,usage) {
 return Object.freeze(Object.fromEntries(library.images.filter(a=>a.card===card&&(!usage||a.usage===usage)&&a.enabled&&a.status==='local').map(a=>[a.source,a.local])));
}
export const unavailableImageSources=card=>new Set(library.images.filter(a=>a.card===card&&a.status==='unavailable').map(a=>a.source));
export const disabledImageSources=card=>new Set(library.images.filter(a=>a.card===card&&!a.enabled).map(a=>a.source));
// Empty pools stay empty. Never substitute another character's image.
export const imageCandidates=(card,usage)=>Object.freeze(Object.keys(imageMap(card,usage)));
