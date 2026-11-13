import test from 'node:test';
import assert from 'node:assert/strict';
import {modelSessionKey} from './session-selection.mjs';
import {pendingOperation} from './model-ui.mjs';
import {playbackSessionId,savedTurnMatches} from './playback-ui.mjs';
const workId='work-'+'a'.repeat(64),v1='version-'+'b'.repeat(64),v2='version-'+'c'.repeat(64),oldId='session-'+'d'.repeat(64),newId='session-'+'e'.repeat(64);
test('workbench references and different published versions remain independent without migration',()=>{
 const data={};const storage={getItem:key=>data[key]??null};
 data[modelSessionKey(workId)]=JSON.stringify(oldId);data[modelSessionKey(workId,v1)]=JSON.stringify(newId);
 assert.equal(modelSessionKey(workId),'modelmirror.authoring.model-session/'+workId);
 assert.equal(playbackSessionId(storage,workId),oldId);assert.equal(playbackSessionId(storage,workId,v1),newId);assert.equal(playbackSessionId(storage,workId,v2),null);
 assert.equal(savedTurnMatches({workId,sessionId:newId},workId,storage,v1),true);
 assert.equal(savedTurnMatches({workId,sessionId:oldId},workId,storage,v1),false);
 assert.equal(savedTurnMatches({workId,sessionId:newId},workId,storage,v2),false);
});
test('unknown model configuration intents stay attached to their original entry',()=>{
 const oldKey='modelmirror.authoring.model-pending/'+workId+'/old';
 const versionKey='modelmirror.authoring.published-model-pending/'+workId+'/'+v1+'/one';
 const record=id=>JSON.stringify({workId,kind:'create',body:{operationId:id}});
 const storage={[oldKey]:record('old'),[versionKey]:record('one'),getItem(key){return this[key]??null;}};
 assert.equal(pendingOperation(storage,workId).body.operationId,'old');assert.equal(pendingOperation(storage,workId,v1).body.operationId,'one');assert.equal(pendingOperation(storage,workId,v2),null);
});
