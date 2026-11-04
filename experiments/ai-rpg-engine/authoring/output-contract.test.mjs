import test from 'node:test';
import assert from 'node:assert/strict';
import {blankDocument,validateDocument,validatePresentation,publicationIssues,canonical,sha256} from './contracts.mjs';

test('输出配置为可选增量；旧文件往返不增加字段，新配置往返不丢失',()=>{
  const old=blankDocument();assert.equal(canonical(validateDocument(old)),canonical(old));
  assert.equal(Object.hasOwn(validateDocument(old).settings,'presentation'),false);
  const next=structuredClone(old);next.settings.presentation={scope:'afengy',imageMappings:[{source:'https://example.test/art',alias:'illustration'}]};
  assert.equal(canonical(validateDocument(next)),canonical(next));
  assert.notEqual(sha256(canonical(next)),sha256(canonical(old)));
});

test('映射拒绝重复、未知字段、超限、凭据和非HTTP来源，不静默归一化URL',()=>{
  const valid={scope:'standard',imageMappings:[{source:'https://EXAMPLE.test/p?q=a%2Fb',alias:'art'}]};
  assert.deepEqual(validatePresentation(valid),valid);
  for(const source of ['javascript:alert(1)','file:///p','data:image/png,x','https://a:b@example.test/p',' https://example.test/p','https://example.test/a b','https://example.test/p#fragment','https:\\example.test/p'])
    assert.throws(()=>validatePresentation({...valid,imageMappings:[{source,alias:'art'}]}),{code:'AUTHORING_IMAGE_SOURCE'},source);
  assert.throws(()=>validatePresentation({...valid,imageMappings:[...valid.imageMappings,...valid.imageMappings]}),{code:'AUTHORING_DUPLICATE'});
  assert.throws(()=>validatePresentation({...valid,sourceOverride:true}),{code:'AUTHORING_UNKNOWN_FIELD'});
  assert.throws(()=>validatePresentation({...valid,scope:'god'}),{code:'AUTHORING_CHOICE_INVALID'});
  assert.throws(()=>validatePresentation({...valid,imageMappings:Array(257).fill(valid.imageMappings[0])}),{code:'AUTHORING_ARRAY_INVALID'});
});

test('发布校验不能把未上传或音频资源宣称为可用的图片映射',()=>{
  const doc=blankDocument();doc.settings.presentation={scope:'afengy',imageMappings:[{source:'https://example.test/p',alias:'art'}]};
  const path='/settings/presentation/imageMappings/0/alias';
  assert.ok(publicationIssues(doc,[]).some(i=>i.code==='AUTHORING_ASSET_MISSING'&&i.path===path));
  assert.ok(publicationIssues(doc,[{alias:'art',mediaType:'audio/mpeg'}]).some(i=>i.code==='AUTHORING_ASSET_ROLE'&&i.path===path));
  assert.ok(!publicationIssues(doc,[{alias:'art',mediaType:'image/png'}]).some(i=>i.path===path));
});
