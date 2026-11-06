import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

// Load the actual JSX exports and shared store into one isolated, in-memory
// module. No browser, mounted editor, project data or production build is used.
const fixturePath=fileURLToPath(new URL('./draft-targets-fixture.cjs',import.meta.url));
const compiled=await build({
 stdin:{contents:"export {unsavedDraftTargets,unsavedDraftCount} from './src/components/ReplacementStages.jsx'; export {DRAFTS} from './src/draftState.js';",resolveDir:fileURLToPath(new URL('..',import.meta.url)),sourcefile:'draft-targets-fixture.js'},
 bundle:true,write:false,format:'cjs',platform:'node',loader:{'.css':'empty'},logLevel:'silent',
});
const fixture=new Module(fixturePath);
fixture._compile(compiled.outputFiles[0].text,fixturePath);
const {DRAFTS,unsavedDraftTargets,unsavedDraftCount}=fixture.exports;
const project={id:'project-one',source_actors:[],roles:[],segments:[{id:'A',name:'第一段'},{id:'C',name:'第三段'}]};
const patch=()=>({working:true,value:{operations:[{first_frame:582,last_frame:582,layer:'edit',tool:'brush',points:[[.2,.3]],radius:8}],fingerprint:'current-material',materialIdentity:'project-one-C'}});

test('patch draft returns to its own segment and the imported-mask repair tab',()=>{
 DRAFTS.clear();DRAFTS.set('project-onemask-patchC',patch());
 assert.deepEqual(unsavedDraftTargets(project),[{label:'第三段的局部遮罩修补',step:2,targetId:'mask-panel',segmentId:'C',maskKind:'edit_mask'}]);
 assert.equal(unsavedDraftCount(project.id),1);
 assert.equal('maskEditor' in unsavedDraftTargets(project)[0],false);
 DRAFTS.clear();
});

test('polygon and local repair drafts retain separate navigation targets and unchanged contents',()=>{
 DRAFTS.clear();
 DRAFTS.set('project-onemaskC',{value:[{time:24.232541666666666,confirmed:false}]});
 DRAFTS.set('project-onemask-workC',{working:true,value:{points:[[.1,.2]]}});
 DRAFTS.set('project-onemask-patchC',patch());
 const before=structuredClone([...DRAFTS]);
 const targets=unsavedDraftTargets(project),polygon=targets.find(target=>target.maskEditor),repair=targets.find(target=>target.maskKind);
 assert.equal(targets.length,2);assert.equal(polygon.segmentId,'C');assert.equal(polygon.maskEditor,true);
 assert.equal(repair.segmentId,'C');assert.equal(repair.maskKind,'edit_mask');assert.equal('maskEditor' in repair,false);
 assert.deepEqual([...DRAFTS],before);
 DRAFTS.clear();
});

test('projects and segments stay isolated, including drafts for unavailable segments',()=>{
 DRAFTS.clear();
 DRAFTS.set('project-onemask-patchA',patch());DRAFTS.set('project-onemask-patchC',patch());
 DRAFTS.set('project-twomask-patchC',patch());DRAFTS.set('project-onemask-patchmissing',patch());
 assert.deepEqual(unsavedDraftTargets(project).map(target=>target.segmentId),['A','C']);
 assert.deepEqual(unsavedDraftTargets({...project,id:'project-two'}).map(target=>target.segmentId),['C']);
 assert.deepEqual(unsavedDraftTargets({...project,segments:[project.segments[0]]}).map(target=>target.segmentId),['A']);
 assert.deepEqual(unsavedDraftTargets({...project,id:'project-three'}),[]);
 DRAFTS.clear();
});

test('empty projects and cleaned repair drafts yield no stale navigation targets',()=>{
 DRAFTS.clear();
 assert.deepEqual(unsavedDraftTargets(null),[]);assert.deepEqual(unsavedDraftTargets(project),[]);
 assert.deepEqual(unsavedDraftTargets({id:project.id}),[]);
 DRAFTS.set('project-onemask-patchC',patch());assert.equal(unsavedDraftTargets(project).length,1);
 DRAFTS.delete('project-onemask-patchC');
 assert.deepEqual(unsavedDraftTargets(project),[]);assert.equal(unsavedDraftCount(project.id),0);
});
