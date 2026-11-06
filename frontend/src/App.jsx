import React,{useEffect,useState,useCallback,useRef} from 'react';
import { toast as notify } from 'sonner';
import { Toaster } from '@/components/ui/sonner';
import { Button as UiButton } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";
import {Plus,X,RefreshCw,AlertCircle,ArrowLeft,ArrowRight} from 'lucide-react';
import {request,upload,time} from './api';
import {readState,startStatePolling} from './stateRead.js';
import {Button,Notice,JobList,Empty,actionName} from './components/Common';
import {MediaPreview,SegmentList,Timeline} from './components/MediaWorkspace';
import {SourceStage,PuppetStage,AssetsStage,GenerateStage,MusicStage} from './components/Stages';
import {Inspector,ConfigDrawer} from './components/Inspector';
import {GenericSourceStage,PeopleStage,SceneStage,ReplacementCheckStage,ReplacementExportStage,ReplacementInspector,unsavedDraftCount,unsavedDraftTargets} from './components/ReplacementStages';
import SeriesWorkspace from './components/SeriesWorkspace';
import {SourceWorkspaceHistory} from './components/SourceWorkspaceSupport';
import {PeopleWorkspaceReference as WorkspaceMediaReference} from './components/PeopleWorkspaceSupport';
import {SceneSourcePreview} from './components/SceneMaskPanel';
import GenerationWorkspaceHelp from './components/GenerationWorkspaceHelp';
import ResultWorkspaceHelp from './components/ResultWorkspaceHelp';
import {revealGenerationDisclosures} from './components/GenerationControls';
import {WorkspaceFrame} from './components/WorkspaceFrame';
import {NewProjectDialog, WorkspaceSupport} from './components/WorkspaceDialogs';
import SourceDraftConflict from './components/SourceDraftConflict';
import {initialWorkspace,workspaceForProject,saveWorkspace,workspaceSearch} from './workspaceNavigation';
import {REPLACEMENT_STEPS,STEP_TASKS,stepScope} from './workflowPresentation';
import {newerProject} from './draftMerge';
import {saveWithRecovery} from './saveRecovery';
import './WorkspaceShell.css';

const legacySteps=[['原片与分段','上传视频 · 切分片段'],['关节人偶','生成运动人偶'],['人物与场景','配置角色与场景包'],['提示词与生成','编写提示词 · 生成视频'],['拼接与音乐','合成片段 · 导出成片']];
const legacyIntros=['原片的时间轴，是动作、镜头与音乐的共同依据。','基于原片生成带关节的运动人偶，用于后续角色与场景的替换。','整理每个角色的外观基准，与场景参考分开绑定。','先核对素材引用和费用，再提交万相 3.0 生成。','去除片段重叠，接入连续原音轨，并检查原片卡点。'];
export default function App(){
 const [state,setState]=useState(null),[project,setProject]=useState(null),[activeId,setActiveId]=useState(null),[step,setStep]=useState(0),[segmentId,setSegmentId]=useState('A'),[mode,setMode]=useState('original'),[currentTime,setCurrentTime]=useState(0),[configOpen,setConfigOpen]=useState(false),[newOpen,setNewOpen]=useState(false),[newName,setNewName]=useState(''),[error,setError]=useState(''),[toast,setToast]=useState(''),[busy,setBusy]=useState(''),[loading,setLoading]=useState(true),[draftCount,setDraftCount]=useState(0);
 const activeIdRef=useRef(null),initial=useRef(initialWorkspace(window.location.search,window.localStorage));
 const [seriesOpen,setSeriesOpen]=useState(false),[navigationRequest,setNavigationRequest]=useState(null),[supportOpen,setSupportOpen]=useState(null);
 const heading=useRef(null);
 const newProjectTrigger=useRef(null),supportTrigger=useRef(null),configTrigger=useRef(null),seriesTrigger=useRef(null);
 function openNewProject(event) {
  newProjectTrigger.current=event.currentTarget;
  setNewOpen(true);
 }
 function openSupport(view,event) {
  supportTrigger.current=event.currentTarget;
  setSupportOpen(view);
 }
 function openSeries(trigger) {
  seriesTrigger.current=trigger?.currentTarget||(trigger?.nodeType===1?trigger:document.activeElement);
  setSeriesOpen(true);
 }
 function openConfig(event) {
  configTrigger.current=event?.currentTarget||(event?.nodeType===1?event:document.activeElement);
  setError('');
  setConfigOpen(true);
 }
 const savePending=useRef(false);
 const [readError,setReadError]=useState(null);
 const [saveConflict,setSaveConflict]=useState(null),[conflictChoices,setConflictChoices]=useState({});
 useEffect(()=>{if(saveConflict&&saveConflict.projectId!==activeId){saveConflict.resolve(null);setSaveConflict(null);}},[activeId,saveConflict]);
 useEffect(()=>{
  if(!navigationRequest)return;
  if(project?.workflow==='replacement'&&[3,4].includes(step)&&navigationRequest.targetId)return;
  // SceneStage reveals its sidebar before focusing these targets.
  if(project?.workflow==='replacement'&&step===2&&(navigationRequest.targetId==='scene-settings'
   ||navigationRequest.sceneDetails||['edit_mask','protection_mask'].includes(navigationRequest.maskKind)))return;
  let frame,cancelled=false;
  function focusTarget(attempt=0){
   if(cancelled||project?.id!==activeIdRef.current)return;
   const roleTarget=navigationRequest.roleId&&step===1?document.querySelector('.people-role-dialog[data-state="open"] .people-role-current'):null;
   if(navigationRequest.roleId&&step===1&&!roleTarget&&attempt<4){frame=requestAnimationFrame(()=>focusTarget(attempt+1));return;}
   const actorTarget=navigationRequest.actorId&&step===1?document.getElementById('actor-'+navigationRequest.actorId):null;
   const el=roleTarget||actorTarget||(navigationRequest.targetId&&document.getElementById(navigationRequest.targetId))||heading.current;
   if(!el)return;
   if(attempt<4&&revealGenerationDisclosures(el)){
    frame=requestAnimationFrame(()=>focusTarget(attempt+1));return;
   }
   el.scrollIntoView({block:'start',behavior:'instant'});
   const headerBottom=document.querySelector('.app-header')?.getBoundingClientRect().bottom||0;
   if(!roleTarget&&el.getBoundingClientRect().top<headerBottom+12)window.scrollBy({top:el.getBoundingClientRect().top-headerBottom-12,behavior:'instant'});
   if(!el.hasAttribute('tabindex'))el.setAttribute('tabindex','-1');
   el.focus({preventScroll:true});
  }
  frame=requestAnimationFrame(()=>focusTarget());
  return()=>{cancelled=true;cancelAnimationFrame(frame);};
 },[navigationRequest,step,project?.id]);
 useEffect(()=>{setSupportOpen(null);},[step,project?.id]);
 const refresh=useCallback(async(id=activeId,signal)=>{const s=await readState(id,{signal});if(signal?.aborted||(id&&activeIdRef.current&&id!==activeIdRef.current))return s;setState(s);if(id&&s.project?.id!==id){const p=await request(`/api/projects/${id}`);if(activeIdRef.current===id)setProject(current=>newerProject(current,p.project||p));}else{setProject(current=>newerProject(current,s.project));if(!activeIdRef.current&&s.project){activeIdRef.current=s.project.id;setActiveId(s.project.id);}}setReadError(null);return s;},[activeId]);
 useEffect(()=>{let live=true,timer,failures=0;const controller=new AbortController();async function boot(){if(activeIdRef.current)return;try{let s;try{s=await readState(initial.current.projectId,{signal:controller.signal});}catch(e){if(!initial.current.projectId||e.status!==404)throw e;s=await readState(null,{signal:controller.signal});}if(!live||activeIdRef.current)return;setState(s);setProject(s.project);setReadError(null);activeIdRef.current=s.project?.id||null;setActiveId(s.project?.id||null);if(s.project){const nav=workspaceForProject(s.project,window.location.search,window.localStorage);setStep(nav.step);setSegmentId(nav.segmentId);setMode(nav.mode);}}catch(e){if(live&&e.name!=='AbortError'){setReadError(e);if(e.retryableRead)timer=setTimeout(boot,Math.min(30000,3000*2**Math.min(failures++,4)));}}finally{if(live)setLoading(false);}}boot();return()=>{live=false;clearTimeout(timer);controller.abort();};},[]);
 useEffect(()=>{if(loading||!project||activeIdRef.current!==project.id)return;const value={step,segmentId,mode};saveWorkspace(window.localStorage,project.id,value);window.history.replaceState(null,'',window.location.pathname+workspaceSearch(project.id,value));},[loading,project?.id,step,segmentId,mode]);
 useEffect(()=>{if(!activeId)return;return startStatePolling({read:signal=>refresh(activeId,signal),onError:setReadError});},[activeId,refresh]);
 useEffect(()=>{const update=()=>setDraftCount(unsavedDraftCount(activeId||''));const leave=e=>{if(unsavedDraftCount()){e.preventDefault();e.returnValue='';}};update();window.addEventListener('frame-studio-drafts',update);window.addEventListener('beforeunload',leave);return()=>{window.removeEventListener('frame-studio-drafts',update);window.removeEventListener('beforeunload',leave);};},[activeId]);
 useEffect(()=>{if(toast){notify.message(toast,{id:'workspace-feedback',duration:4500});const id=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(id);}},[toast]);
 useEffect(()=>{if(project&&!project.segments?.some(s=>s.id===segmentId))setSegmentId(project.segments?.[0]?.id||'');},[project?.id,project?.segments,segmentId]);
 async function safe(fn,label){setError('');if(label)setBusy(label);try{return await fn();}catch(e){if(e.method==='GET'&&e.path==='/api/state')setReadError(e);else setError(e.message);return null;}finally{if(label)setBusy('');}}
 const patch=(partial,draft)=>{
  if(savePending.current){setError('已有保存正在处理，请先处理当前保存；其余草稿仍保留');return Promise.resolve(null);}
  savePending.current=true;
  return safe(async()=>{
  const pid=project.id;
  if(activeIdRef.current!==pid)throw new Error('正在切换项目，请稍候再保存');
  const p=await saveWithRecovery({project,partial:{expected_revision:project.revision,...partial},draft,
   read:()=>request(`/api/projects/${pid}`),
   write:body=>{if(activeIdRef.current!==pid)throw new Error('已切换项目，原项目草稿仍保留');return request(`/api/projects/${pid}`,{method:'PUT',body});},
   onLatest:latest=>{if(activeIdRef.current===pid)setProject(current=>newerProject(current,latest));},
   choose:conflicts=>new Promise(resolve=>{setConflictChoices({});setSaveConflict({projectId:pid,conflicts,resolve});})});
  if(!p)return null;
  if(activeIdRef.current===pid){setProject(current=>newerProject(current,p.project||p));if(project.workflow==='replacement'&&(step===0||step===3))await refresh(pid);setToast('已保存到本机项目');}
  return p;
  },'save').finally(()=>{savePending.current=false;});
 };
 const onUpload=(kind,files,extra={})=>safe(async()=>{if(activeIdRef.current!==project.id)throw new Error('正在切换项目，请稍候再导入');for(const file of files)await upload(project.id,kind,file,extra);await refresh(project.id);setToast(`${files.length} 个文件已导入`);},'upload');
 const onImport=payload=>safe(async()=>{if(activeIdRef.current!==project.id)throw new Error('正在切换项目，请稍候再导入');await request(`/api/projects/${project.id}/import`,{method:'POST',body:payload});await refresh(project.id);setToast('本机文件已导入');},'import');
 const onAction=(action,extra={})=>safe(async()=>{if(activeIdRef.current!==project.id)throw new Error('正在切换项目，请稍候再操作');const response=await request(`/api/projects/${project.id}/actions`,{method:'POST',body:{action,segment_id:segmentId,expected_revision:project.revision,...extra}});await refresh(project.id);setToast(response.job_id?'任务已加入本地处理队列':'操作已完成');return response;},action);
 const onGeneration=payload=>safe(async()=>{if(activeIdRef.current!==project.id)throw new Error('正在切换项目，请稍候再操作');const result=await request(`/api/projects/${project.id}/generation`,{method:'POST',body:{...payload,...(payload.action==='prepare'?{expected_revision:project.revision}:{})}});await refresh(project.id);return result;},'generation');
 async function selectProject(id){activeIdRef.current=id;setActiveId(id);await safe(async()=>{const p=await request(`/api/projects/${id}`);if(activeIdRef.current!==id)return;const next=p.project||p;setProject(current=>newerProject(current,next));const nav=workspaceForProject(next,'',window.localStorage);setSegmentId(nav.segmentId);setStep(nav.step);setMode(nav.mode);setNavigationRequest({targetId:null});setCurrentTime(next.segments?.find(s=>s.id===nav.segmentId)?.start||0);await refresh(id);},'project');}
 async function create(e){e.preventDefault();await safe(async()=>{const p=await request('/api/projects',{method:'POST',body:{name:newName.trim()||'未命名项目'}});const created=p.project||p;activeIdRef.current=created.id;setActiveId(created.id);setProject(created);setNewOpen(false);setNewName('');setStep(0);setMode('puppet');setNavigationRequest({targetId:null});await refresh(created.id);},'create');}
 const segment=project?.segments?.find(s=>s.id===segmentId)||project?.segments?.[0];
 const jobs=(state?.jobs||[]).filter(j=>!j.project_id||j.project_id===project?.id).sort((a,b)=>(b.created_at||'').localeCompare(a.created_at||''));
 const replacement=project?.workflow==='replacement';
 const sourceTask=replacement&&step===0;
 const peopleTask=replacement&&step===1,focusedTask=sourceTask||peopleTask;
 const sceneTask=replacement&&step===2;
 const materialTask=replacement&&step===3;
 const resultTask=replacement&&step===4;
 const replacementTask=focusedTask||sceneTask||materialTask||resultTask;
 const steps=replacement?REPLACEMENT_STEPS:legacySteps;
 const intros=replacement?['以原片为动作和时间依据，选定本批唯一交付档位。','同一原片人物跨镜头使用固定编号；交叉和遮挡位置单独确认。','保留运动空间和遮挡关系，明确可替换与受保护的范围。','准备已保存材料，集中确认要求与本次费用，再生成并保存各段结果。','逐段检查原始回片并对照验收，按原切点接回连续原声。']:legacyIntros;
 function selectSegment(id){setSegmentId(id);setMode('puppet');setCurrentTime(project?.segments?.find(s=>s.id===id)?.start||0);}
 function changeStep(n,targetId,target={}){if(target.segmentId&&project?.segments?.some(item=>item.id===target.segmentId))selectSegment(target.segmentId);setNavigationRequest({...target,step:n,targetId});setStep(n);if(n===3&&replacement)refresh(project.id).catch(setReadError);if(replacement)setMode('puppet');else if(n===0)setMode('original');else if(n===1)setMode('puppet');}
 if(loading)return <div role="status" className="loading-screen flex"><Spinner /><h1>帧工坊</h1><p>正在打开本地工作台…</p></div>;
 const scope=stepScope(step),task=STEP_TASKS[step];
 const runningJobs=jobs.filter(j=>['QUEUED','RUNNING','PROCESSING'].includes(j.status));
 const stage=project&&(replacement?<>
  {step===0&&<GenericSourceStage project={project} deliverySpec={state?.project?.id===project.id&&state.project.revision===project.revision?state?.generation?.compatibility?.delivery_spec:null} busy={!!busy} onPatch={patch} onUpload={onUpload} onImport={onImport} onNext={()=>changeStep(1)} selectedId={segment?.id} onSelectSegment={selectSegment} saveError={error} fileDetailsRequest={navigationRequest?.compatibility?navigationRequest:null} navigationRequest={navigationRequest} />}
  {step===1&&<PeopleStage draftTarget={navigationRequest} key={project.id} project={project} segment={segment} onPatch={patch} onUpload={onUpload} onImport={onImport} onOpenSeries={openSeries} onSelectSegment={selectSegment} onPrevious={()=>changeStep(0)} onNext={()=>changeStep(2)} draftCount={draftCount} busy={!!busy} error={error} />}
  {step===2&&<SceneStage draftTarget={navigationRequest} key={project.id} project={project} segment={segment} onPatch={patch} onUpload={onUpload} onImport={onImport} onAction={onAction} onSelectSegment={selectSegment} onOpenSupport={openSupport} onPrevious={()=>changeStep(1)} onNext={()=>changeStep(3)} draftCount={draftCount} busy={!!busy} error={error} />}
  {step===3&&(project.id!==activeId||state?.project?.id!==project.id?<p role="status">正在切换项目…</p>:<ReplacementCheckStage navigationRequest={navigationRequest} onOpenSupport={openSupport} key={project.id} project={project} segment={segment} onPatch={patch} onAction={onAction} onStep={changeStep} onSelectSegment={selectSegment} onConfig={openConfig} onHistory={()=>setSupportOpen('history')} config={state?.config} busy={!!busy} error={error} jobs={jobs} generation={state?.generation} onGeneration={onGeneration} draftCount={draftCount} dirtyDraftTargets={unsavedDraftTargets(project)} />)}
  {step===4&&(project.id!==activeId||state?.project?.id!==project.id?<p role="status">正在切换项目…</p>:<ReplacementExportStage navigationRequest={navigationRequest} onOpenSupport={openSupport} key={project.id} resultStates={state?.generation?.result_states} runs={state?.generation?.runs} onSelectSegment={selectSegment} error={error} dirtyDraftTargets={unsavedDraftTargets(project)} busy={busy} materialConfirmation={state?.generation?.material_confirmation} project={project} segment={segment} onAction={onAction} onUpload={onUpload} onImport={onImport} onStep={changeStep} jobs={jobs} />)}
 </>:<>
  <Notice>当前是历史人偶项目；新建项目使用人物与场景替换流程。云端上传与付费生成等待确认。</Notice>
  <MediaPreview project={project} segment={segment} mode={mode} setMode={setMode} onTime={setCurrentTime} />
  <SegmentList project={project} segments={project.segments||[]} selectedId={segment?.id} onSelect={selectSegment} />
  {step===0&&<SourceStage project={project} onPatch={patch} onUpload={onUpload} onImport={onImport} />}
  {step===1&&<PuppetStage project={project} segment={segment} onPatch={patch} onAction={onAction} onUpload={onUpload} onImport={onImport} />}
  {step===2&&<AssetsStage project={project} onPatch={patch} onUpload={onUpload} onImport={onImport} onAction={onAction} />}
  {step===3&&<GenerateStage project={project} segment={segment} onPatch={patch} onAction={onAction} onUpload={onUpload} jobs={jobs} />}
  {step===4&&<MusicStage project={project} segment={segment} onPatch={patch} onAction={onAction} onUpload={onUpload} onImport={onImport} />}
 </>);
 const inspector=project&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&(replacement?<ReplacementInspector step={step} project={project} segment={segment} onPatch={patch} onAction={onAction} onStep={changeStep} jobs={jobs} busy={busy} config={state?.config} onConfig={openConfig} />:<Inspector project={project} segment={segment} onPatch={patch} onAction={onAction} onStep={changeStep} jobs={jobs} busy={busy} config={state?.config} onConfig={openConfig} />);
 function finishConflict(choices){
  saveConflict.resolve(choices);
  setSaveConflict(null);
 }
 const overlays=<>
  {saveConflict&&<SourceDraftConflict conflicts={saveConflict.conflicts} choices={conflictChoices} onChoice={(key,choice)=>setConflictChoices(current=>({...current,[key]:choice}))} onCancel={()=>finishConflict(null)} onConfirm={finishConflict} />}
  {replacementTask&&supportOpen&&(!focusedTask&&!sceneTask||supportOpen==='history'||sceneTask&&supportOpen==='reference')&&<WorkspaceSupport title={{reference:sceneTask||materialTask||resultTask?'原片预览':'原片与时间轴',history:'处理记录',help:'使用说明'}[supportOpen]} onClose={()=>setSupportOpen(null)} returnFocus={supportTrigger} className={supportOpen==='reference'?(sceneTask||materialTask||resultTask?'source-workspace-support source-step-support scene-reference-support':'source-workspace-support people-workspace-reference-support'):focusedTask||sceneTask?'source-workspace-support source-step-support':'source-workspace-support'} closeSize="3">
   {(sceneTask||materialTask||resultTask)&&supportOpen==='reference'&&<SceneSourcePreview project={project} segment={segment} />}
   {!sourceTask&&!sceneTask&&!materialTask&&!resultTask&&supportOpen==='reference'&&<WorkspaceMediaReference project={project} segment={segment} mode={mode} setMode={setMode} onSelect={selectSegment} currentTime={currentTime} onTime={setCurrentTime} />}
   {supportOpen==='history'&&<SourceWorkspaceHistory project={project} jobs={jobs} compact={focusedTask||sceneTask||materialTask||resultTask} />}
   {supportOpen==='help'&&resultTask&&<ResultWorkspaceHelp />}
   {supportOpen==='help'&&materialTask&&<GenerationWorkspaceHelp />}
  </WorkspaceSupport>}
  {seriesOpen&&<SeriesWorkspace project={project} returnFocus={seriesTrigger} onProjectRefresh={async()=>{const result=await refresh(activeIdRef.current);return result.project;}} onSelectProject={async id=>{setSeriesOpen(false);await selectProject(id);}} onClose={()=>setSeriesOpen(false)} />}
  <Toaster duration={4500} />
  {configOpen&&<ConfigDrawer returnFocus={configTrigger} error={error} config={state?.config} system={state?.system} onClose={()=>setConfigOpen(false)} onSave={config=>safe(async()=>{await request('/api/config',{method:'POST',body:config});await refresh();setToast('API 连接配置已保存，尚未验证权限');},'config')} />}
  {newOpen&&<NewProjectDialog name={newName} onNameChange={setNewName} onClose={()=>setNewOpen(false)} onSubmit={create} busy={busy==='create'} error={error} returnFocus={newProjectTrigger} />}
 </>;
 return <WorkspaceFrame className={`${replacement?'replacement-workspace':''} ${focusedTask?'source-workspace':''} ${sourceTask?'source-columns-workspace':''} ${peopleTask?'people-workspace people-columns-workspace':''} ${sceneTask?'scenes-workspace scene-columns-workspace':''} ${materialTask?'generation-workspace generation-columns-workspace':''} ${resultTask?'result-workspace result-columns-workspace':''}`} replacement={replacement} projects={state?.projects} activeId={activeId} projectCaption={focusedTask&&project?.series_link?`第 ${project.series_link.episode_number} 集`:undefined} onSelectProject={selectProject} onOpenSeries={openSeries} onNewProject={openNewProject} steps={steps} step={step} onStepChange={changeStep} onOpenConfig={openConfig} configured={state?.config?.configured} inspector={inspector} overlays={overlays}>
     {replacement&&project&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<section className="project-context" aria-label="当前项目与制作目标">
      <div><span className="context-label">当前项目{project.series_link?` · 第 ${project.series_link.episode_number} 集`:' · 独立视频'}</span><strong>{project.name}</strong></div>
      <p>制作目标：替换选定人物与场景，保留原片动作、镜头、节奏和原声。</p>
      <div className="project-context-meta"><span>原片：{project.source?.name||'尚未导入'}</span>{project.source&&<span>{time(project.source_meta?.duration||0)} · {project.segments?.length||0} 个片段 · {project.source_actors?.length||0} 名人物</span>}<span className="local-only-state">结果复核与导出在本机完成</span></div>
     </section>}
     <div className={sourceTask?'workspace-heading source-page-heading':peopleTask?'workspace-heading people-page-heading':sceneTask?'workspace-heading scene-page-heading':materialTask?'workspace-heading generation-page-heading':resultTask?'workspace-heading result-page-heading':'workspace-heading'} data-testid="workspace-heading" ref={heading} tabIndex="-1">
      <div>{replacement&&!replacementTask&&<span className="context-label">第 {step+1} 步，共 5 步</span>}<h1 className="text-2xl font-semibold tracking-tight">{sourceTask?'原片与分段':peopleTask?'人物替换':sceneTask?'场景与范围':materialTask?'生成视频':resultTask?'结果与导出':steps[step][0]}</h1>{!replacementTask&&<p>{replacement?task[0]:intros[step]}</p>}</div>
      <UiButton type="button" className="shell-refresh" aria-label="刷新项目状态" onClick={()=>safe(()=>refresh(),'refresh')} variant={replacementTask?'outline':'ghost'} size="icon" disabled={(busy==='refresh')} aria-busy={(busy==='refresh') || undefined}>{(busy==='refresh') && <Spinner />}<RefreshCw size={18} /></UiButton>
     </div>
     {readError&&<Alert className="shell-callout" role="status" data-tone="warning"><AlertCircle size={18} /><AlertDescription><strong>项目状态暂未更新</strong><p>{readError.message}</p><small>当前内容与未保存修改仍保留；这里只重新读取状态，不重发保存、上传或生成请求。</small>{replacementTask?<UiButton type="button" onClick={()=>refresh(activeIdRef.current||initial.current.projectId).catch(setReadError)} variant="outline" size="lg">重新读取状态</UiButton>:<Button onClick={()=>refresh(activeIdRef.current||initial.current.projectId).catch(setReadError)}>重新读取状态</Button>}</AlertDescription></Alert>}
     {error&&!materialTask&&!resultTask&&<Alert className="shell-callout" role="alert" variant="destructive"><AlertCircle size={18} /><AlertDescription><strong>本次操作未完成</strong><p>{error.includes("No such file or directory")?"找不到本机文件。请检查文件路径和名称，确认文件仍在原位置。":error}</p><small>请按提示修正后重试。已保存的材料可继续在本机查看。</small>{replacementTask?<UiButton type="button" onClick={()=>setError('')} aria-label="关闭错误提示" variant="outline" size="icon"><X size={18} /></UiButton>:<UiButton type="button" variant="ghost" size="icon" onClick={()=>setError('')} aria-label="关闭错误提示"><X size={16} /></UiButton>}</AlertDescription></Alert>}
     {project?<>
      {replacement&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<div className="task-context"><div><strong>{scope[0]}</strong><p>{scope[1]}</p></div><div><span>本步产出</span><strong>{task[1]}</strong></div></div>}
      {!materialTask&&!resultTask&&!peopleTask&&!sceneTask&&draftCount>0&&(!sourceTask||draftCount>unsavedDraftCount(project.id+'segments'))&&(materialTask?<p className="material-global-draft" role="status">有 {draftCount} 处未保存修改，请先保存后再生成。</p>:<Notice>有 {draftCount} 处未保存修改，尚未进入材料与审核清单。切换步骤可继续编辑；刷新或关闭页面前请先保存。</Notice>)}
      {!materialTask&&!resultTask&&runningJobs.length>0&&<Alert className="processing-feedback" data-tone="info" role="status" aria-live="polite"><Spinner /><AlertDescription><strong>{actionName(runningJobs[0].action)} · {runningJobs[0].status==='QUEUED'?'等待本机处理':'正在本机处理'}</strong><span>{runningJobs[0].message||(focusedTask||sceneTask?'完成后自动更新。':materialTask?'完成后自动更新，可在“处理记录”查看详情。':'完成后会更新项目状态，详细记录在本页下方。')}</span></AlertDescription></Alert>}
      {replacement&&step>=2&&!sceneTask&&!materialTask&&!resultTask&&project.segments?.length>0&&<section className="task-segment-picker" aria-label="选择当前处理片段"><div className="section-top"><div><h2>当前处理片段</h2><p>{step===2?'每个片段分别保存替换与保护范围。':step===3?'切换片段，核对各自的参考图与提示词。':'切换片段，核对自动保存或导入的生成结果。'}</p></div></div><SegmentList compact project={project} segments={project.segments} selectedId={segment?.id} onSelect={selectSegment} /></section>}
      <div className="current-task-content">{stage}</div>

      {!replacement&&<Timeline project={project} selectedId={segment?.id} onSelect={selectSegment} currentTime={currentTime} />}
      {!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<JobList project={project} jobs={jobs} />}
      {replacementTask&&<>
       {!materialTask&&!resultTask&&['FAILED','UNKNOWN'].includes(jobs[0]?.status)&&<Alert className={sceneTask?'scene-job-notice':peopleTask?'people-job-notice':'source-job-notice'} variant={(jobs[0].status==='FAILED'?'red':'amber') === "red" ? "destructive" : "default"} data-tone={(jobs[0].status==='FAILED'?'red':'amber') === "amber" ? "warning" : undefined}>
        <AlertCircle size={18} />
        <AlertDescription>{actionName(jobs[0].action)}：{jobs[0].message||'请查看处理记录，确认结果后重试。'}
        <UiButton type="button" onClick={event=>openSupport('history',event)} variant="outline" size="lg">查看处理记录</UiButton></AlertDescription>
       </Alert>}
      </>}
     </>:<Empty><span>替换原视频中的人物或场景，从新建项目、导入原片开始。</span><Button variant="default" onClick={openNewProject}><Plus size={16} />新建项目并开始</Button></Empty>}
 </WorkspaceFrame>;
}
