import test from 'node:test';
import assert from 'node:assert/strict';
import {panel,applyNote} from './records.mjs';
import {source} from '../revisions/test-support.mjs';
import {apply} from '../revisions/contracts.mjs';
test('editing a cited revision invalidates its record; unrelated edits and appends retain it',()=>{
 const f=apply(null,{action:'create',operationId:'root',expectedRevision:null,payload:{source:source()}});
 const b=f.branches[f.rootId],ref={id:'m1',revisionId:'r1'},next=(action,p)=>applyNote(f,action,{operationId:'change-'+f.revision,expectedRevision:f.revision,payload:{branchId:b.id,actor:'actor',...p}});
 next('draft',{noteId:null,owner:null,kind:'summary',origin:'experience',refs:[ref],content:'确认',mode:'fixed',words:[]});
 next('adopt',{noteId:b.notes[0].noteId});
 const status=()=>panel(b,'actor').records.at(-1).status;
 b.messages[2].revisionId='unrelated-revision';assert.equal(status(),'valid');
 b.messages.push({...b.messages[3],id:'extra',revisionId:'extra-r'});assert.equal(status(),'valid');
 b.messages[1].revisionId='changed-evidence';assert.equal(status(),'invalid');
});