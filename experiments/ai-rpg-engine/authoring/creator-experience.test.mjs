import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {startAuthoringServer} from './server.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-creator-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function open(t,creatorExperience){const directory=await mkdtemp(join(root,'case-'));const host=await startAuthoringServer({directory,creatorExperience});t.after(()=>host.close());return host;}
test('creator entry is opt in; preserved entries serve the original shell and no new guide',async t=>{
 const host=await open(t,false);const html=await(await fetch(host.origin+'/')).text();assert.doesNotMatch(html,/data-experience="creator"/);assert.doesNotMatch(html,/creator-experience.css/);assert.equal((await fetch(host.origin+'/creator-guide.html')).status,404);
});
test('creator shell and static guide do not mutate work state or expose model execution',async t=>{
 const host=await open(t,true);const before=await(await fetch(host.origin+'/rpg-app/api/authoring/state')).json();
 const page=await fetch(host.origin+'/'),html=await page.text();assert.match(html,/data-experience="creator"/);assert.match(html,/creator-guide.html/);assert.doesNotMatch(html,/<script[^>]*>[^<]+/);
 const guide=await fetch(host.origin+'/creator-guide.html');assert.equal(guide.status,200);assert.match(guide.headers.get('content-security-policy'),/connect-src 'self'/);const text=await guide.text();assert.doesNotMatch(text,/<script[ >]/i);assert.doesNotMatch(text,/<(?:img|iframe|audio)[ >]/i);
 const ids=new Set([...text.matchAll(/id="([^" ]+)"/g)].map(m=>m[1]));for(const [,target]of text.matchAll(/href="#([^" ]+)"/g))assert.ok(ids.has(target),target);
 assert.equal((await fetch(host.origin+'/creator-experience.css')).status,200);assert.deepEqual(await(await fetch(host.origin+'/rpg-app/api/authoring/state')).json(),before);
});
test('guide routes reject cross-origin and arbitrary paths',async t=>{const host=await open(t,true);assert.equal((await fetch(host.origin+'/creator-guide.html',{headers:{Origin:'https://example.org'}})).status,403);assert.equal((await fetch(host.origin+'/creator-guide.html?file=secret')).status,422);assert.equal((await fetch(host.origin+'/creator-guide.mjs')).status,404);});
test('feature flag cannot be enabled by a truthy string',async()=>{await assert.rejects(startAuthoringServer({directory:root,creatorExperience:'true'}),e=>e.code==='AUTHORING_SERVER_OPTIONS');});
import {parse} from 'parse5';
import {compilePresentation} from './presentation.mjs';
import {projectSuggestions} from './input-assistance.mjs';
const walk=node=>[node,...(node.childNodes||[]).flatMap(walk)];
const nodeText=node=>node.nodeName==='#text'?node.value:(node.childNodes||[]).map(nodeText).join('');
test('tutorial examples run through the real display compiler and suggestion parser',async()=>{
 const guide=walk(parse(await readFile(new URL('./creator-guide.html',import.meta.url),'utf8')));
 const example=id=>nodeText(guide.find(n=>n.attrs?.some(a=>a.name==='id'&&a.value===id)));
 const opening=example('example-opening'),reply=example('example-reply'),css=example('example-css');
 const options={origin:'http://127.0.0.1:18600',token:'a'.repeat(48)};
 const initial=compilePresentation({...options,mode:'opening',html:opening});
 assert.deepEqual(initial.manifest.diagnostics,[]);
 const tree=walk(parse(initial.frame));
 assert.ok(tree.some(n=>n.tagName==='textarea'&&n.attrs.some(a=>a.name==='id'&&a.value==='opening-text')));
 assert.ok(tree.some(n=>n.tagName==='button'&&n.attrs.some(a=>a.name==='data-rpg-input-from'&&a.value==='opening-text')));
 const answer=compilePresentation({...options,mode:'reply',html:reply,css});
 assert.deepEqual(answer.manifest.diagnostics,[]);assert.ok(answer.frame.includes(css));
 assert.ok(walk(parse(answer.frame)).some(n=>n.tagName==='details'));
 const suggestions=projectSuggestions(example('example-suggestions'),true);
 assert.equal(suggestions.choices.length,3);
 assert.equal(guide.filter(n=>n.tagName==='script').length,0);
});
