import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWorkspaceQuery,workspaceForProject,saveWorkspace,workspaceSearch,initialWorkspace} from '../src/workspaceNavigation.js';
function memory(){const entries=new Map();return {getItem:k=>entries.get(k),setItem:(k,v)=>entries.set(k,v)};}
const project={id:'goldcase',workflow:'replacement',segments:[{id:'A'},{id:'B'}]};
test('new replacement projects default to selected submission segment, never full source',()=>{
 assert.deepEqual(workspaceForProject(project,'',memory()),{step:0,segmentId:'A',mode:'puppet'});
});
test('review link reopens requested episode segment and view',()=>{
 const search=workspaceSearch('goldcase',{step:3,segmentId:'B',mode:'puppet'});
 assert.deepEqual(workspaceForProject(project,search,memory()),{step:3,segmentId:'B',mode:'puppet'});
 assert.equal(parseWorkspaceQuery('?project=goldcase&step=review').step,3);
});
test('episode navigation remembers each episode independently',()=>{
 const storage=memory();saveWorkspace(storage,'goldcase',{step:3,segmentId:'B',mode:'puppet'});saveWorkspace(storage,'episode2',{step:1,segmentId:'S2',mode:'compare'});
 assert.equal(initialWorkspace('',storage).projectId,'episode2');
 assert.deepEqual(workspaceForProject(project,'',storage),{step:3,segmentId:'B',mode:'puppet'});
});
test('explicit full source is retained only when deliberately chosen',()=>{
 const storage=memory();saveWorkspace(storage,'goldcase',{step:2,segmentId:'A',mode:'original'});
 assert.equal(workspaceForProject(project,'',storage).mode,'original');
 assert.equal(workspaceForProject(project,'?project=goldcase&view=puppet',storage).mode,'puppet');
});
test('bad or deleted segment and malformed preferences safely fall back',()=>{
 const storage=memory();storage.setItem('frame-studio-workspace-v2','broken');
 assert.equal(workspaceForProject(project,'?project=goldcase&step=999&segment=gone&view=unknown',storage).segmentId,'A');
 assert.equal(parseWorkspaceQuery('?project=../../secret').projectId,null);
 assert.doesNotThrow(()=>saveWorkspace({setItem(){throw Error('private')}},'goldcase',{step:1}));
});
