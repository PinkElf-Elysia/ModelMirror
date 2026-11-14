import {blankCard,validateCard,problem} from './card.mjs';
export const CHARACTER_DOCUMENT='modelmirror.rpg.character-document/1';
export function characterDocument(blank,card=blankCard(),source=null){
 const doc=structuredClone(blank);doc.format=CHARACTER_DOCUMENT;doc.kind='character';doc.character={card:validateCard(card),source};
 doc.metadata.name=card.data.name.slice(0,120);doc.metadata.tags=[];return doc;
}
export function validateCharacterDocument(doc,validateLegacy){
 if(doc.format!==CHARACTER_DOCUMENT||doc.kind!=='character'||!doc.character||Object.keys(doc.character).sort().join()!=='card,source')problem('CHARACTER_DOCUMENT_INVALID');
 validateCard(doc.character.card);
 const ref=doc.character.source;
 if(ref!==null&&(!ref||Object.keys(ref).sort().join()!=='container,selection,sha256,size'||!/^[a-f0-9]{64}$/.test(ref.sha256)||!Number.isSafeInteger(ref.size)||ref.size<1||ref.size>32*1024*1024||!['json','png'].includes(ref.container)||![null,'chara','ccv3'].includes(ref.selection)))problem('CHARACTER_SOURCE_INVALID');
 const {kind,character,...legacy}=doc;legacy.format='modelmirror.rpg.authoring-document/1';validateLegacy(legacy);
 if(doc.metadata.name!==doc.character.card.data.name.slice(0,120))problem('CHARACTER_METADATA_MISMATCH');
 // Standard prompts never pass through the world-card system/prefix/suffix assembly.
 if(Object.values(doc.sources).some(Boolean)||doc.worldbook.entries.length||doc.enhancements.dialogue.opening||doc.enhancements.dialogue.presets.length||doc.enhancements.dialogue.openings?.length)problem('CHARACTER_WORLD_FIELDS_FORBIDDEN');
 return structuredClone(doc);
}
export function characterPublicationIssues(doc,assets){
 const d=doc.character.card.data,issues=[];
 if(!d.name.trim())issues.push({code:'CHARACTER_NAME_REQUIRED',path:'/character/card/data/name'});
 if(![d.first_mes,...d.alternate_greetings].some(x=>x.trim()))issues.push({code:'CHARACTER_GREETING_REQUIRED',path:'/character/card/data/first_mes'});
 const cover=doc.settings.media.cover;
 if(cover&&!assets.some(a=>a.alias===cover&&a.mediaType.startsWith('image/')))issues.push({code:'AUTHORING_ASSET_MISSING',path:'/settings/media/cover'});
 return issues;
}
