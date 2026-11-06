const matchesHash=(asset,hash)=>!!asset&&typeof asset.sha256==='string'&&asset.sha256.length>0&&typeof hash==='string'&&hash.length>0&&asset.sha256===hash;

function maskFileState(asset,recordedHash,optional=false){
 const present=!!asset;
 const matchesReport=matchesHash(asset,recordedHash);
 return {
  asset:asset||null,
  assetId:asset?.id||null,
  present,optional,matchesReport,
  // Both imports and renders use the same asset kind. A hash match identifies
  // a recorded render; an unmatched registered file has no proven provenance.
  source:present?matchesReport?'rendered':'provided':'missing',
  label:present?matchesReport?'本地制作文件':'已有文件 · 来源待核对':optional?'未提供（可选）':'待制作或导入',
  note:present?matchesReport?'文件与本地制作记录一致，仍需检查轮廓与遮挡。':'可使用已有文件；来源和技术条件需另行核对。':optional?'仅在需要恢复原片前景时提供。':'可手工制作，或导入已有逐帧黑白文件。',
 };
}

export function maskMaterialState(segment={}){
 const report=segment.mask_render_report||null;
 const edit=maskFileState(segment.edit_mask,report?.edit_mask_sha256);
 const protection=maskFileState(segment.protection_mask,report?.protection_mask_sha256,true);
 const previewAsset=segment.mask_preview||null;
 const previewFileMatchesReport=matchesHash(previewAsset,report?.preview_sha256);
 const previewCorresponds=edit.matchesReport&&protection.matchesReport&&previewFileMatchesReport;
 const preview={
  asset:previewAsset,
  assetId:previewAsset?.id||null,
  present:!!previewAsset,
  matchesReport:previewCorresponds,
  fileMatchesReport:previewFileMatchesReport,
  source:previewAsset?previewFileMatchesReport?'rendered':'provided':'missing',
  label:previewAsset?previewCorresponds?'与当前文件对应':'旧检查预览，仅供参考':'尚无检查预览',
  note:previewAsset?previewCorresponds?'上次制作与当前编辑遮罩、保护遮罩及预览文件对应；不随新关键帧编辑更新，仍须人工检查。':'当前文件未全部对应制作记录，预览不代表当前替换范围。':'外部导入文件可以直接保留，无需为了预览补画关键帧。',
 };
 const keyframes=segment.mask_keyframes||[];
 const confirmed=keyframes.filter(frame=>frame.confirmed===true).length;
 return {
  edit,protection,preview,
  needsEditFile:!edit.present,
  keyframes:{total:keyframes.length,confirmed,pending:keyframes.length-confirmed,label:keyframes.length?`${keyframes.length} 帧已保存 · ${keyframes.length-confirmed} 帧待确认`:edit.present?'无手工关键帧，可使用已有文件':'未保存手工关键帧'},
  record:{present:!!report,report,label:report?previewCorresponds?'制作记录与当前文件对应':'已有制作记录，文件需核对':'尚无轮廓制作记录',note:'制作记录不代表技术检查或人工复核通过。'},
 };
}
