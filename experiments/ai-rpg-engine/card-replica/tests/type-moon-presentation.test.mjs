import assert from 'node:assert/strict';
import atlas from '../../type-moon-card/resources/dialogue-atlas.json' with {type:'json'};
import test from 'node:test';
import {cardPresentation} from '../lib/card-presentation.mjs';
import {streamPresentation} from '../lib/stream-presentation.mjs';
import {typeMoonResponse} from '../lib/type-moon-presentation.mjs';

test('型月对白只使用身份匹配的本地立绘，保留未知人物与御主对白',()=>{
  const raw='<p>她转过身。</p>'+
    '<say class="npc" data-speaker="所长" data-portrait="奥尔加玛丽-FGO" data-face="angry"><button class="say-avatar"></button>准备好了吗？</say>'+
    '<say class="npc" data-speaker="陌生人" data-portrait="阿尔托莉雅-未登记职阶-XTEST">还不能说。</say>'+
    '<say class="npc" data-speaker="术之从者" data-portrait="阿尔托莉雅-Caster-FGO">出发。</say>'+
    '<say class="user" data-speaker="你"><button class="user-avatar"><img src="https://example.invalid/user.png"></button>我知道了。</say>';
  const projected=typeMoonResponse({session:{turns:[{raw}]}});
  const turn=projected.session.turns[0];
  assert.equal(turn.raw,raw);
  assert.match(turn.html,/class="tm-avatar/);assert.match(turn.html,/src="\/rpg-app\/type-moon\/(?:portraits|dialogue)\//);
  assert.doesNotMatch(turn.html,/npc-03\.webp|https:|data-portrait|data-face/);
  for(const line of ['她转过身。','所长','准备好了吗？','陌生人','还不能说。','我知道了。'])assert.ok(turn.html.includes(line));
  assert.equal(streamPresentation(raw,'type-moon'),turn.html);
  assert.doesNotMatch(cardPresentation(raw,'archive').html,/tm-avatar|npc-26/);
});
test('对白身份匹配遵循作品、职阶与显式别名，按钮不会重放模型内容',()=>{
  const raw='<say class="npc" data-speaker="陌生人" data-portrait="沙条绫香-FSF"><button onclick="danger()">外部控件</button>在这里。</say>'+
    '<say class="npc" data-speaker="少女" data-portrait="伊莉雅-FSN">你好。</say>';
  const html=streamPresentation(raw,'type-moon');
  assert.doesNotMatch(html,/npc-18|danger|外部控件/);assert.match(html,/class="tm-avatar/);
  assert.match(html,/在这里。/);
});

test('表情差分按照完整角色键及登记别名选取，缺失表情使用平静帧',()=>{
  const render=(key,emotion)=>streamPresentation('<say class="npc" data-speaker="同伴" data-portrait="'+key+'" data-face="'+emotion+'">在这里。</say>','type-moon');
  const mash=atlas.characters[atlas.aliases['玛修-FGO']];
  assert.ok(mash.avatar&&mash.portrait);
  for(const [i,face] of ['happy','angry','sad','laugh','neutral','confused'].entries()){
    const html=render('玛修-FGO',face);
    assert.ok(html.includes(mash.avatar)&&html.includes(mash.portrait));
    assert.ok(html.includes('tm-frame-'+i));
  }
  assert.match(render('玛修-FGO','未登记表情'),/tm-frame-4/);
  assert.notEqual(atlas.characters['远坂凛-FZ'].portrait,atlas.characters['远坂凛-FSN'].portrait);
  assert.ok(render('远坂凛-FZ','happy').includes(atlas.characters['远坂凛-FZ'].portrait));
  assert.ok(render('远坂凛-FSN','happy').includes(atlas.characters['远坂凛-FSN'].portrait));
  const alias=Object.entries(atlas.aliases).find(([key,target])=>!Object.hasOwn(atlas.characters,key)&&atlas.characters[target]?.avatar);
  assert.ok(alias);
  assert.equal(render(alias[0],'sad'),render(alias[1],'sad'));
  const special=Object.entries(atlas.characters).find(([,entry])=>entry.untrusted);
  assert.ok(special);
  const masked=render(special[0],'untrusted');
  assert.ok(masked.includes(special[1].untrusted.portrait));
  assert.match(masked,/tm-frame-4/);
  assert.doesNotMatch(render('不存在的职阶-未知世界','happy'),/<img|<button/);
});
