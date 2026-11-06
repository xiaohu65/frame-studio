import test from 'node:test';
import assert from 'node:assert/strict';
import {maskMaterialState} from '../src/scenePresentation.js';

const asset=(id,sha256,kind)=>({id,sha256,kind,url:`/api/media/project/${id}`,name:`${id}.mkv`});
const edit=asset('edit','edit-hash','edit_mask');
const protection=asset('protection','protect-hash','protection_mask');
const preview=asset('preview','preview-hash','mask_preview');
const report={status:'MANUAL_POLYGON_MASKS_NEED_REVIEW',edit_mask_sha256:edit.sha256,protection_mask_sha256:protection.sha256,preview_sha256:preview.sha256,binary_masks_verified:true,review_required:true,identity_quality:'UNVERIFIED',motion_quality:'UNVERIFIED',keyframe_count:2};
const generated={id:'A',edit_mask:edit,protection_mask:protection,mask_preview:preview,mask_render_report:report,mask_keyframes:[{time:0,confirmed:true,regions:[]},{time:1,confirmed:true,regions:[]}]};

test('an imported edit mask without manual keyframes remains a supported material path',()=>{
 const state=maskMaterialState({edit_mask:edit,mask_keyframes:[]});
 assert.equal(state.edit.present,true);
 assert.equal(state.edit.source,'provided');
 assert.equal(state.edit.matchesReport,false);
 assert.equal(state.needsEditFile,false);
 assert.equal(state.keyframes.total,0);
 assert.match(state.keyframes.label,/可使用已有文件/);
 assert.equal(state.protection.present,false);
 assert.equal(state.protection.optional,true);
 assert.equal(state.protection.label,'未提供（可选）');
 assert.equal(state.preview.present,false);
 assert.equal(state.record.present,false);
 assert.doesNotMatch(state.edit.label,/通过/);
});

test('all three matching report hashes associate a preview with the current material files',()=>{
 const state=maskMaterialState(generated);
 assert.equal(state.edit.asset,edit);
 assert.equal(state.edit.assetId,'edit');
 assert.equal(state.edit.source,'rendered');
 assert.equal(state.edit.matchesReport,true);
 assert.equal(state.protection.source,'rendered');
 assert.equal(state.protection.matchesReport,true);
 assert.equal(state.preview.fileMatchesReport,true);
 assert.equal(state.preview.matchesReport,true);
 assert.equal(state.preview.label,'与当前文件对应');
 assert.match(state.preview.note,/不随新关键帧编辑更新/);
 assert.equal(state.record.label,'制作记录与当前文件对应');
 assert.doesNotMatch(state.edit.label+state.protection.label+state.preview.label,/检查通过|审核通过/);
 assert.match(state.record.note,/不代表/);
});

test('replacing just one mask preserves the other source association and marks the old preview separately',()=>{
 for(const kind of ['edit_mask','protection_mask']){
  const replacement=asset(`imported-${kind}`,`different-${kind}`,kind);
  const state=maskMaterialState({...generated,[kind]:replacement});
  const changed=kind==='edit_mask'?state.edit:state.protection;
  const unchanged=kind==='edit_mask'?state.protection:state.edit;
  assert.equal(changed.assetId,replacement.id);
  assert.equal(changed.source,'provided');
  assert.equal(changed.matchesReport,false);
  assert.equal(unchanged.source,'rendered');
  assert.equal(unchanged.matchesReport,true);
  assert.equal(state.preview.fileMatchesReport,true);
  assert.equal(state.preview.matchesReport,false);
  assert.equal(state.preview.label,'旧检查预览，仅供参考');
 }
});

test('a stored preview without a report or without its own matching hash is only historical evidence',()=>{
 const noReport=maskMaterialState({...generated,mask_render_report:null});
 assert.equal(noReport.preview.present,true);
 assert.equal(noReport.preview.matchesReport,false);
 assert.equal(noReport.preview.label,'旧检查预览，仅供参考');
 const replacedPreview=maskMaterialState({...generated,mask_preview:asset('other-preview','different-preview','mask_preview')});
 assert.equal(replacedPreview.edit.matchesReport,true);
 assert.equal(replacedPreview.protection.matchesReport,true);
 assert.equal(replacedPreview.preview.fileMatchesReport,false);
 assert.equal(replacedPreview.preview.matchesReport,false);
 assert.equal(replacedPreview.preview.label,'旧检查预览，仅供参考');
});

test('missing optional protection does not demand drawing but cannot establish a three-file preview match',()=>{
 const state=maskMaterialState({...generated,protection_mask:null,mask_keyframes:[]});
 assert.equal(state.needsEditFile,false);
 assert.equal(state.protection.optional,true);
 assert.equal(state.protection.present,false);
 assert.equal(state.preview.matchesReport,false);
 assert.equal(state.preview.label,'旧检查预览，仅供参考');
 assert.equal(state.keyframes.total,0);
 assert.match(state.keyframes.label,/可使用已有文件/);
});

test('file IDs and asset kinds alone never prove a connection to a render report',()=>{
 const noHashes=maskMaterialState({...generated,edit_mask:{...edit,sha256:undefined}});
 assert.equal(noHashes.edit.assetId,edit.id);
 assert.equal(noHashes.edit.matchesReport,false);
 assert.equal(noHashes.edit.source,'provided');
 assert.equal(noHashes.preview.matchesReport,false);
 const incomplete=maskMaterialState({...generated,mask_render_report:{edit_mask_sha256:'',protection_mask_sha256:protection.sha256,preview_sha256:preview.sha256}});
 assert.equal(incomplete.edit.matchesReport,false);
 assert.equal(incomplete.preview.matchesReport,false);
 const newIds=maskMaterialState({...generated,edit_mask:{...edit,id:'same-bytes-new-id'}});
 assert.equal(newIds.edit.matchesReport,true,'the real report stores content hashes, not asset IDs');
});

test('keyframe counts use saved records and strict confirmation without turning them into validation',()=>{
 const state=maskMaterialState({mask_keyframes:[{confirmed:true},{confirmed:false},{confirmed:'true'},{}]});
 assert.equal(state.keyframes.total,4);
 assert.equal(state.keyframes.confirmed,1);
 assert.equal(state.keyframes.pending,3);
 assert.equal(state.needsEditFile,true);
 assert.equal(state.edit.label,'待制作或导入');
 assert.equal('ready' in state,false);
 assert.equal('approved' in state,false);
 assert.equal(maskMaterialState().keyframes.total,0);
});

test('presentation preserves imported assets, old reports, and exact annotation data without mutation',()=>{
 const input=structuredClone({...generated,edit_mask:asset('new-import','new-hash','edit_mask'),mask_keyframes:[{time:1.234567891234,confirmed:false,regions:[{id:'outline',mode:'edit',points:[[.123456,.2],[.4,.2],[.4,.8]]}]}]});
 const before=structuredClone(input);
 function freeze(value){if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;}
 freeze(input);
 const state=maskMaterialState(input);
 assert.deepEqual(input,before);
 assert.equal(state.edit.asset,input.edit_mask);
 assert.equal(state.record.report,input.mask_render_report);
 assert.equal(state.keyframes.pending,1);
 assert.equal(state.preview.label,'旧检查预览，仅供参考');
});
