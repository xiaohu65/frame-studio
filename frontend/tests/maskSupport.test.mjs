import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const fixturePath=fileURLToPath(new URL('./mask-support-fixture.cjs',import.meta.url));
const compiled=await build({stdin:{contents:"export {importedMaskDraftTargets} from './src/components/SceneMaskEditor.jsx';export {DRAFTS} from './src/draftState.js';",resolveDir:fileURLToPath(new URL('..',import.meta.url)),sourcefile:'mask-support-fixture.js'},bundle:true,write:false,format:'cjs',platform:'node',logLevel:'silent'});
const fixture=new Module(fixturePath);fixture._compile(compiled.outputFiles[0].text,fixturePath);
const {importedMaskDraftTargets,DRAFTS}=fixture.exports;
test('retired support drafts remain reachable for explicit discard and navigate to their own imported-mask panel without changing legacy patch or polygon drafts',()=>{
 DRAFTS.clear();const value={working:true,value:{versionId:'local-version',operations:[{role:'foreground',first_frame:251,last_frame:251}],reviews:[],binding:{candidate_id:'B'}}};
 DRAFTS.set('pmask-supportB',value);DRAFTS.set('pmask-patchA',{working:true,value:{operations:[{layer:'edit'}]}});DRAFTS.set('othermask-supportB',{working:true,value:{}});
 const before=structuredClone([...DRAFTS]),project={id:'p',segments:[{id:'A',name:'A段'},{id:'B',name:'B段'}]};
 const targets=importedMaskDraftTargets(project);
 assert.equal(targets.length,2);assert.equal(targets[1].segmentId,'B');assert.equal(targets[1].maskKind,'edit_mask');assert.equal('maskEditor' in targets[1],false);
 assert.deepEqual([...DRAFTS],before);DRAFTS.clear();
});
