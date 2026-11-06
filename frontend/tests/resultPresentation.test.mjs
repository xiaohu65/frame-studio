import test from 'node:test';
import assert from 'node:assert/strict';
import {ORIGINAL_DELIVERY_POLICY,resultDraftKey,readResultDraft,writeResultDraft,clearResultDraft,resultSegmentState,resultProjectState,resultSource,resultEvidence} from '../src/resultPresentation.js';
const segment={id:'A',generated:{id:'candidate-A',url:'/raw.mp4'}};
const row={candidate_current:true,candidate_asset_id:segment.generated.id,validation_current:true,review_current:true,candidate_fingerprint:'current'};
const records={delivery_policy:ORIGINAL_DELIVERY_POLICY,segments:{A:row}};
test('saved files and legacy review evidence do not authorize the original candidate',()=>{
 assert.equal(resultSegmentState(segment,{}).reviewed,false);
 assert.equal(resultSegmentState(segment,{segments:{A:{...row,composite_current:true}}}).reviewed,false);
 assert.equal(resultSegmentState(segment,records).reviewed,true);
 assert.equal(resultSegmentState({...segment,candidate_stale:true},records).reviewed,false);
 assert.equal(resultSegmentState({...segment,generated:{id:'other'}},records).reviewed,false);
});
test('result states go from original candidate validation straight to acceptance',()=>{
 assert.equal(resultSegmentState({id:'A'},records).label,'待导入');
 assert.equal(resultSegmentState(segment,{...records,segments:{A:{...row,validation_current:false}}}).label,'待检查');
 assert.equal(resultSegmentState(segment,{...records,segments:{A:{...row,review_current:false}}}).label,'待验收');
 assert.equal(resultSegmentState(segment,records).label,'已验收');
});
test('review drafts survive switching and use candidate plus version identity',()=>{
 const key=resultDraftKey('p',segment,records.segments.A);
 writeResultDraft(key,{checks:{identity:true},editing:true});
 const other=resultDraftKey('p',{id:'B',generated:{id:'candidate-B'}},{candidate_fingerprint:'b'});
 assert.deepEqual(readResultDraft(other).checks,{});
 assert.equal(readResultDraft(key).checks.identity,true);
 assert.deepEqual(readResultDraft(resultDraftKey('p',segment,{candidate_fingerprint:'changed'})).checks,{});
 clearResultDraft(key);assert.deepEqual(readResultDraft(key).checks,{});
});
test('export requires current material confirmation and all current result reviews',()=>{
 const project={segments:[segment],final:{id:'final'}};
 assert.equal(resultProjectState(project,records,{current:false}).exportReady,false);
 assert.equal(resultProjectState(project,records,{current:true}).exportReady,true);
 assert.equal(resultProjectState({...project,segments:[segment,{id:'B'}]},records,{current:true}).exportReady,false);
 assert.equal(resultProjectState(project,records,{current:true}).finalCurrent,false);
 assert.equal(resultProjectState(project,{...records,final_current:true},{current:true}).finalCurrent,true);
});
test('provenance belongs to the actual received asset',()=>{
 assert.equal(resultSource({...segment,generation_provenance:{asset_id:'old'}}),'手动导入');
 assert.equal(resultSource({...segment,generation_provenance:{asset_id:'candidate-A'}}),'API 自动接入');
 assert.equal(resultSource({...segment,candidate_adaptation:{asset_id:'candidate-A'}}),'历史适配结果');
});

test('raw receipt evidence separates usage and coverage without turning precheck into acceptance',()=>{
 const current={...segment,generation_provenance:{asset_id:segment.generated.id,run_id:'run-1'}};
 const pending=resultSegmentState(current,{...records,segments:{A:{...row,validation_current:false,review_current:false}}});
 const usage={output_video_duration:10.03};
 const missing={source_first_frame:301,source_last_frame:301,source_first_seconds:10,source_last_seconds:10};
 const run={id:'run-1',segments:[{segment_id:'A',asset:segment.generated,usage,return_inspection:{
  actual:{frame_count:300,decoded_duration_seconds:10},target:{frame_count:301},
  coverage:{missing_frame_count:1,missing_ranges:[missing]},issues:[{message:'少一帧'}],
 }}]};
 const evidence=resultEvidence(current,pending,[run]);
 assert.equal(evidence.report,null);
 assert.equal(pending.reviewed,false);
 assert.equal(evidence.actual.decoded_duration_seconds,10);
 assert.equal(evidence.usage.output_video_duration,10.03);
 assert.equal(evidence.delta,-1);
 assert.deepEqual(evidence.coverage.missing_ranges,[missing]);
 assert.equal(resultEvidence({...current,generated:{id:'replacement'}},pending,[run]).usage,undefined);
});

test('result page renders all six only for pending acceptance, completed review is reused',async()=>{
 const {build}=await import('esbuild');
 const {fileURLToPath}=await import('node:url');const {createRequire}=await import('node:module');
 const React=(await import('react')).default;const {renderToStaticMarkup}=await import('react-dom/server');
 const require=createRequire(import.meta.url);
 const compiled=await build({entryPoints:[fileURLToPath(new URL('../src/components/ReplacementStages.jsx',import.meta.url))],bundle:true,platform:'node',format:'cjs',write:false,loader:{'.css':'empty'},external:['react','react-dom','lucide-react','radix-ui']});
 const module={exports:{}};new Function('require','module','exports',compiled.outputFiles[0].text)(require,module,module.exports);
 const Stage=module.exports.ReplacementExportStage;
 const seg={...segment,name:'甲段',start:0,end:2,composited:{id:'master'},composite_preview:{id:'preview',url:'/old-preview.mp4'},reference:{id:'silent-reference',url:'/silent.mp4'}};
 const project={id:'ui-review',segments:[seg],source:{id:'source',url:'/source.mp4'},source_meta:{fps_rational:'30/1',has_audio:true}};
 const render=(s=records,p=project,runs=[])=>renderToStaticMarkup(React.createElement(Stage,{project:p,segment:p.segments[0],resultStates:s,materialConfirmation:{current:true},jobs:[],runs}));
 const saved=render();assert.ok(saved.includes('本段验收已保存'));assert.ok(saved.includes('重新验收'));
 assert.equal((saved.match(/role="checkbox"/g)||[]).length,0);
 assert.doesNotMatch(saved,/检查并合成当前段|候选本地适配/);
 const pending=render({...records,segments:{A:{...row,review_current:false,reference_current:true}}});
 assert.equal((pending.match(/role="checkbox"/g)||[]).length,6);assert.ok(!/<button\b(?=[^>]*role="checkbox")(?=[^>]*aria-checked="true")/.test(pending));
 assert.ok(pending.includes('勾选中 0 / 6 · 尚未保存'));
 assert.match(pending,/<video\b[^>]*src="\/source\.mp4"/);
 assert.match(pending,/<video\b[^>]*src="\/raw\.mp4"[^>]*muted=""/);
 assert.doesNotMatch(pending,/<video\b[^>]*src="\/(old-preview|silent)\.mp4"/);
 assert.match(pending,/href="\/old-preview\.mp4"/);
 const unchecked=render({...records,segments:{A:{...row,validation_current:false}}});
 assert.match(unchecked,/检查当前段<\/button>/);
 assert.doesNotMatch(unchecked,/id="candidate-review"/);
 assert.match(unchecked,/id="candidate-preview"/);
 assert.match(unchecked,/独立字幕轨/);
 assert.match(unchecked,/正式无字幕母版/);
 assert.doesNotMatch(unchecked,/data-testid="assemble-replacement-final"|保存本段验收/);
 const failedSeg={...seg,generation_provenance:{asset_id:seg.generated.id,run_id:'bound-run'},candidate_validation:{
  fingerprint:row.candidate_fingerprint,candidate_asset_id:seg.generated.id,ready:false,source:{frame_count:301,source_start:0,source_end:301/30},
  candidate:{width:1280,height:720,fps_rational:'30/1',frame_count:300,decoded_duration_seconds:10,first_pts_rational:'0',last_pts_rational:'299/30'},
  coverage:{missing_frame_count:1,missing_ranges:[{source_first_frame:301,source_last_frame:301,source_first_seconds:10}]},
  observations:['F151 手部遮挡提前'],
 }};
 const failed=render({...records,segments:{A:{...row,validation_current:false,review_current:false}}},
  {...project,segments:[failedSeg]},[{id:'bound-run',segments:[{segment_id:'A',asset:seg.generated,usage:{output_video_duration:10.03}}]}]);
 assert.match(failed,/技术检查 · <strong>未通过/);
 assert.match(failed,/300 \/ 301 帧 · 少 1 帧/);
 assert.match(failed,/原片 F301 · 10\.000000 秒/);
 assert.match(failed,/平台计费用量/);
 assert.match(failed,/10\.030 秒/);
 assert.match(failed,/F151 手部遮挡提前/);
 assert.doesNotMatch(failed,/data-testid="assemble-replacement-final"|保存本段验收/);
 const legacy=render({segments:{A:{...row,composite_current:true}}});
 assert.doesNotMatch(legacy,/<video\b|id="candidate-review"|data-testid="assemble-replacement-final"/);
 const empty=render({segments:{A:{candidate_current:false}}},{...project,segments:[{id:'A',start:0,end:2}]});
 assert.ok(empty.includes('导入当前段结果'));assert.ok(!empty.includes('candidate-review'));
 const source=renderToStaticMarkup(React.createElement(module.exports.GenericSourceStage,{project:{...project,
  source_meta:{...project.source_meta,width:1920,height:1080,duration:301/30},settings:{resolution:'720P'}},
  deliverySpec:{ratio:'16:9',resolution:'720P',width:1280,height:720,fps:'30',frame_count:301,duration_seconds:301/30,
   source_frame_count:301,coverage_verified:false,dimension_basis:'observed'}}));
 assert.match(source,/原片宽高<\/dt><dd[^>]*>1920 × 1080/);
 assert.match(source,/目标宽高<\/dt><dd[^>]*>1280 × 720/);
 assert.match(source,/<button\b(?=[^>]*role="combobox")(?=[^>]*aria-label="交付档位")[^>]*>[^]*?720P[^]*?<\/button>/);
 assert.match(source,/报价时解码核对/);
});

test('queued review saves reconcile by job and draft identity across editor unmounts and segments',async()=>{
 const {trackResultSubmission,reconcileResultSubmissions}=await import('../src/resultPresentation.js');
 writeResultDraft('a',{checks:{identity:true},editing:true});writeResultDraft('b',{checks:{action:true},editing:true});
 trackResultSubmission('a','save-a');trackResultSubmission('b','save-b');
 assert.deepEqual(reconcileResultSubmissions([{id:'save-a',status:'SUCCEEDED'},{id:'save-b',status:'RUNNING'}]),['a']);
 assert.deepEqual(readResultDraft('a').checks,{});assert.equal(readResultDraft('b').checks.action,true);
 assert.deepEqual(reconcileResultSubmissions([{id:'save-b',status:'FAILED'}]),[]);
 assert.equal(readResultDraft('b').checks.action,true,'failed save retains exact draft');clearResultDraft('b');
});

test('step 5 existing status label follows saved acceptance and current export evidence',async()=>{
 const {workflowRecordLabels}=await import('../src/workflowPresentation.js');
 const project={segments:[segment]};
 assert.equal(workflowRecordLabels(project,{result_states:records,material_confirmation:{current:true}})[4],'各段已验收 · 可导出');
 assert.equal(workflowRecordLabels({...project,final:{id:'old'}},{result_states:{segments:{A:{candidate_current:false}}}})[4],'当前段结果待更新');
 assert.equal(workflowRecordLabels({...project,final:{id:'new'}},{result_states:{...records,final_current:true}})[4],'成片已导出');
});

test('step 4 status does not claim confirmation is ready after settings discarded local preparation',async()=>{
 const {workflowRecordLabels}=await import('../src/workflowPresentation.js');
 const plan={ready:true,fingerprint:'same',issues:[]},generation={preparation:{status:'READY',plan},quote:{id:'old',fingerprint:'same'},material_confirmation:{fingerprint:'same'},compatibility:{ready:true,issues:[]}};
 const project={source:{id:'source'},segments:[{id:'A'}]};
 assert.equal(workflowRecordLabels(project,generation,{configured:true})[3],'准备需要更新');
 assert.equal(workflowRecordLabels({...project,preparation:{fingerprint:'same'}},generation,{configured:true})[3],'待核对材料与费用');
});
