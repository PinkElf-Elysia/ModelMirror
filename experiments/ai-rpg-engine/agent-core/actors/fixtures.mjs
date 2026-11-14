import {blankDocument} from '../../authoring/contracts.mjs';
import {characterDocument} from '../../character/document.mjs';
import {hash} from '../contracts.mjs';

// Original adult fixture, created through the merged authoring/version API.
// No existing private character, conversation or exploration data is imported.
export async function ensureCharacter(works){
 const document=characterDocument(blankDocument());
 Object.assign(document.character.card.data,{
  name:'林舟',description:'林舟，二十八岁的药圃看护，做事细心。只根据亲见与获准查阅的资料回答。',
  personality:'说话温和简洁，重视记录，不夸大药效。',
  scenario:'在药圃的休息廊与成年同行交谈；不预设发生过服药、赠与或承诺。',
  first_mes:'廊下可以歇一会儿。你想先聊些什么？',
  mes_example:'{{user}}：我听说你知道全部账目。\n{{char}}：我只知道获准查阅的部分。',
  creator_notes:'原创成年中性测试夹具，不是正式角色素材。',character_version:'r6-fixture-1'
 });document.metadata.name='林舟';
 const created=await works.create({operationId:'r6-character-create',expectedRevision:null,document});
 const published=await works.publish(created.workId,{operationId:'r6-character-publish',expectedRevision:0});
 const reference={workId:created.workId,versionId:published.result.versionId};
 const config=await works.verifyConfiguration(reference.workId,reference.versionId);
 return {reference,configurationHash:hash(config)};
}
