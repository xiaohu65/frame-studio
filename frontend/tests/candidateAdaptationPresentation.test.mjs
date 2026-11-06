import test from 'node:test';
import assert from 'node:assert/strict';
import {localAdaptationSource,candidateSampleState} from '../src/candidateAdaptationPresentation.js';

test('local adaptation requires exact supported rational metadata',()=>{
 for(const fps_rational of ['24000/1001','48000/2002'])assert.equal(localAdaptationSource({fps_rational}),true);
 for(const meta of [{fps:23.976},{fps_rational:'23.976'},{fps_rational:'24/1'},{fps_rational:'30/1'},{fps_rational:'24000/0'},{},null])assert.equal(localAdaptationSource(meta),false);
});

test('a legacy local preview never replaces the current raw sample or its inspection status',()=>{
 const raw={id:'raw-sample',sha256:'raw-sha'};
 const segment={sample_candidate:raw,sample_provenance:{asset_id:raw.id,fingerprint:'materials',purpose:'candidate_sample'},
  sample_preview:{id:'preview',sha256:'preview-sha'},sample_preview_report:{material_fingerprint:'materials',candidate_asset_id:'raw-sample',preview_asset_id:'preview',preview_sha256:'preview-sha'}};
 const current=candidateSampleState(segment,'materials');
 assert.equal(current.current,true);
 assert.equal(current.raw,raw);
 assert.equal(current.status.code,'sample-received');
 assert.equal(Object.hasOwn(current,'preview'),false);
 assert.equal(candidateSampleState(segment,'older').current,false);
 assert.equal(candidateSampleState({...segment,sample_candidate:{id:'different'}},'materials').current,false);
});

test('raw identity and matching audition survive mask-only changes and ignore legacy composite flags',()=>{
 const raw={id:'raw',sha256:'raw-sha'},preview={id:'preview',sha256:'preview-sha'};
 const segment={sample_candidate:raw,sample_preview:preview,sample_preview_report:{material_fingerprint:'paid-version',timeline_size_validation:{ready:true}},
  sample_provenance:{asset_id:raw.id,fingerprint:'paid-version',purpose:'candidate_sample',return_inspection:{timeline_adaptation:{ready:true}},
   audition:{audio_asset:{id:'audio',sha256:'audio-sha'},candidate_asset_id:raw.id,candidate_sha256:raw.sha256,material_fingerprint:'paid-version'}}};
 const local={candidate_asset_id:raw.id,candidate_sha256:raw.sha256,input_current:true,binding_fingerprint:'new-local-mask',reuse_available:true,
  preview_current:false,adaptation_current:true,audition_current:true,audition_asset_id:'audio',audition_sha256:'audio-sha'};
 const before=candidateSampleState(segment,'new-material-version',local);
 assert.equal(before.current,true);assert.equal(before.raw,raw);
 assert.equal(before.status.code,'sample-inspected');assert.ok(before.audition);
 const updated={...local,reuse_available:false,local_binding_current:true,preview_current:true,preview_asset_id:preview.id,preview_sha256:preview.sha256};
 const after=candidateSampleState(segment,'new-material-version',updated);
 assert.deepEqual(after,before);
 assert.equal(candidateSampleState(segment,'paid-version',{...local,audition_sha256:'other-audio'}).audition,null);
 for(const changed of [{input_current:false},{candidate_asset_id:'other'},{candidate_sha256:'other-sha'}]){
  const stale=candidateSampleState(segment,'paid-version',{...local,...changed});
  assert.equal(stale.current,false,'a server mismatch defeats a current-looking old global fingerprint');
  assert.equal(stale.audition,null);
  assert.equal(stale.status.code,'sample-input-stale');
 }
});
