// Eligibility uses the exact rational metadata; rounded fps values cannot opt in.
export function localAdaptationSource(meta={}){
 const match=String(meta?.fps_rational||'').match(/^(\d+)\/(\d+)$/);
 return !!match&&BigInt(match[2])>0n&&BigInt(match[1])*1001n===24000n*BigInt(match[2]);
}

export function sampleResultStatus(input={},inspection){
 const timeline=inspection?.timeline_adaptation;
 if(input.input_current===false)return {code:'sample-input-stale',label:'原始候选仅作历史保留',tone:'warning'};
 if(timeline?.status==='BLOCKED')return {code:'sample-blocked',label:'原始候选已接收 · 时间轴覆盖未通过',tone:'warning'};
 if(timeline?.ready===true)return {code:'sample-inspected',label:'原始候选已接收 · 时间轴覆盖通过',tone:'neutral'};
 return {code:'sample-received',label:'原始候选已接收 · 实际规格待检查',tone:'neutral'};
}

export function candidateSampleState(segment={},fingerprint='',serverState){
 segment=segment||{};
 const provenance=segment.sample_provenance||{},raw=segment.sample_candidate||null;
 const authoritative=!!raw&&!!serverState&&serverState.candidate_asset_id===raw.id&&!!raw.sha256&&serverState.candidate_sha256===raw.sha256;
 const current=serverState?authoritative&&serverState.input_current===true:!!raw&&!!fingerprint&&provenance.fingerprint===fingerprint&&provenance.asset_id===raw.id&&provenance.purpose==='candidate_sample';
 const inspection=provenance.return_inspection||null;
 const audition=provenance.audition||null;
 const auditionCurrent=current&&!!audition?.audio_asset&&(serverState?
  serverState.audition_current===true&&serverState.audition_asset_id===audition.audio_asset.id&&serverState.audition_sha256===audition.audio_asset.sha256:
  audition.material_fingerprint===fingerprint&&audition.candidate_asset_id===raw.id&&audition.candidate_sha256===raw.sha256);
 const reason=serverState?.candidate_asset_id===raw?.id?serverState?.reason:null;
 return {raw,provenance,current,reason,inspection,status:sampleResultStatus({input_current:current},inspection),audition:auditionCurrent?audition:null};
}
