import React,{useEffect,useState,useCallback,useRef} from 'react';
import {Settings,ChevronDown,ChevronRight,Plus,X,RefreshCw,FolderOpen,Video,AlertCircle} from 'lucide-react';
import {request,upload,time} from './api';
import {readState,startStatePolling} from './stateRead.js';
import {Button,Notice,JobList,Empty,actionName} from './components/Common';
import {MediaPreview,SegmentList,Timeline} from './components/MediaWorkspace';
import {SourceStage,PuppetStage,AssetsStage,GenerateStage,MusicStage} from './components/Stages';
import {Inspector,ConfigDrawer} from './components/Inspector';
import {GenericSourceStage,PeopleStage,SceneStage,ReplacementCheckStage,ReplacementExportStage,ReplacementInspector,ReplacementWorkflowGuide,unsavedDraftCount,unsavedDraftTargets} from './components/ReplacementStages';
import SeriesWorkspace from './components/SeriesWorkspace';
import {initialWorkspace,workspaceForProject,saveWorkspace,workspaceSearch} from './workspaceNavigation';
import {REPLACEMENT_STEPS,STEP_TASKS,workflowRecordLabels,stepScope} from './workflowPresentation';
import {newerProject} from './draftMerge';
import {saveWithRecovery} from './saveRecovery';
import './WorkspaceShell.css';

const legacySteps=[['原片与分段','上传视频 · 切分片段'],['关节人偶','生成运动人偶'],['人物与场景','配置角色与场景包'],['提示词与生成','编写提示词 · 生成视频'],['拼接与音乐','合成片段 · 导出成片']];
const legacyIntros=['原片的时间轴，是动作、镜头与音乐的共同依据。','基于原片生成带关节的运动人偶，用于后续角色与场景的替换。','整理每个角色的外观基准，与场景参考分开绑定。','先核对素材引用和费用，再提交万相 3.0 生成。','去除片段重叠，接入连续原音轨，并检查原片卡点。'];
export default function App(){
 const [state,setState]=useState(null),[project,setProject]=useState(null),[activeId,setActiveId]=useState(null),[step,setStep]=useState(0),[segmentId,setSegmentId]=useState('A'),[mode,setMode]=useState('original'),[currentTime,setCurrentTime]=useState(0),[configOpen,setConfigOpen]=useState(false),[newOpen,setNewOpen]=useState(false),[newName,setNewName]=useState(''),[error,setError]=useState(''),[toast,setToast]=useState(''),[busy,setBusy]=useState(''),[loading,setLoading]=useState(true),[draftCount,setDraftCount]=useState(0);
 const activeIdRef=useRef(null),initial=useRef(initialWorkspace(window.location.search,window.localStorage));
 const [seriesOpen,setSeriesOpen]=useState(false),[navigationRequest,setNavigationRequest]=useState(null),[previewOpen,setPreviewOpen]=useState(false),[supportOpen,setSupportOpen]=useState(null);
 const heading=useRef(null);
 const savePending=useRef(false);
 const [readError,setReadError]=useState(null);
 const [saveConflict,setSaveConflict]=useState(null),[conflictChoices,setConflictChoices]=useState({});
 useEffect(()=>{if(saveConflict&&saveConflict.projectId!==activeId){saveConflict.resolve(null);setSaveConflict(null);}},[activeId,saveConflict]);
 useEffect(()=>{
  if(!navigationRequest)return;
  let frame,cancelled=false;
  function focusTarget(attempt=0){
   if(cancelled||project?.id!==activeIdRef.current)return;
   const roleTarget=navigationRequest.roleId&&step===1?document.querySelector('.people-role-dialog[open] .people-role-current'):null;
   if(navigationRequest.roleId&&step===1&&!roleTarget&&attempt<4){frame=requestAnimationFrame(()=>focusTarget(attempt+1));return;}
   const actorTarget=navigationRequest.actorId&&step===1?document.getElementById('actor-'+navigationRequest.actorId):null;
   const el=roleTarget||actorTarget||(navigationRequest.targetId&&document.getElementById(navigationRequest.targetId))||heading.current;
   if(!el)return;
   if(navigationRequest.sceneDetails)document.querySelector('#scene-settings .scene-global-details')?.setAttribute('open','');
   if(navigationRequest.compatibility)document.querySelector('#source-import .source-file-details')?.setAttribute('open','');
   for(let parent=el;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
   el.scrollIntoView({block:'start',behavior:'instant'});
   const headerBottom=document.querySelector('.app-header')?.getBoundingClientRect().bottom||0;
   if(!roleTarget&&el.getBoundingClientRect().top<headerBottom+12)window.scrollBy({top:el.getBoundingClientRect().top-headerBottom-12,behavior:'instant'});
   if(!el.hasAttribute('tabindex'))el.setAttribute('tabindex','-1');
   el.focus({preventScroll:true});
  }
  frame=requestAnimationFrame(()=>focusTarget());
  return()=>{cancelled=true;cancelAnimationFrame(frame);};
 },[navigationRequest,step,project?.id]);
 useEffect(()=>{setPreviewOpen(false);setSupportOpen(null);},[step,project?.id]);
 const refresh=useCallback(async(id=activeId,signal)=>{const s=await readState(id,{signal});if(signal?.aborted||(id&&activeIdRef.current&&id!==activeIdRef.current))return s;setState(s);if(id&&s.project?.id!==id){const p=await request(`/api/projects/${id}`);if(activeIdRef.current===id)setProject(current=>newerProject(current,p.project||p));}else{setProject(current=>newerProject(current,s.project));if(!activeIdRef.current&&s.project){activeIdRef.current=s.project.id;setActiveId(s.project.id);}}setReadError(null);return s;},[activeId]);
 useEffect(()=>{let live=true,timer,failures=0;const controller=new AbortController();async function boot(){if(activeIdRef.current)return;try{let s;try{s=await readState(initial.current.projectId,{signal:controller.signal});}catch(e){if(!initial.current.projectId||e.status!==404)throw e;s=await readState(null,{signal:controller.signal});}if(!live||activeIdRef.current)return;setState(s);setProject(s.project);setReadError(null);activeIdRef.current=s.project?.id||null;setActiveId(s.project?.id||null);if(s.project){const nav=workspaceForProject(s.project,window.location.search,window.localStorage);setStep(nav.step);setSegmentId(nav.segmentId);setMode(nav.mode);}}catch(e){if(live&&e.name!=='AbortError'){setReadError(e);if(e.retryableRead)timer=setTimeout(boot,Math.min(30000,3000*2**Math.min(failures++,4)));}}finally{if(live)setLoading(false);}}boot();return()=>{live=false;clearTimeout(timer);controller.abort();};},[]);
 useEffect(()=>{if(loading||!project||activeIdRef.current!==project.id)return;const value={step,segmentId,mode};saveWorkspace(window.localStorage,project.id,value);window.history.replaceState(null,'',window.location.pathname+workspaceSearch(project.id,value));},[loading,project?.id,step,segmentId,mode]);
 useEffect(()=>{if(!activeId)return;return startStatePolling({read:signal=>refresh(activeId,signal),onError:setReadError});},[activeId,refresh]);
 useEffect(()=>{const update=()=>setDraftCount(unsavedDraftCount(activeId||''));const leave=e=>{if(unsavedDraftCount()){e.preventDefault();e.returnValue='';}};update();window.addEventListener('frame-studio-drafts',update);window.addEventListener('beforeunload',leave);return()=>{window.removeEventListener('frame-studio-drafts',update);window.removeEventListener('beforeunload',leave);};},[activeId]);
 useEffect(()=>{if(toast){const id=setTimeout(()=>setToast(''),4500);return()=>clearTimeout(id);}},[toast]);
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
  if(activeIdRef.current===pid){setProject(current=>newerProject(current,p.project||p));if(step===3&&project.workflow==='replacement')await refresh(pid);setToast('已保存到本机项目');}
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
 const steps=replacement?REPLACEMENT_STEPS:legacySteps;
 const intros=replacement?['保留原片动作、镜头、节奏与原声，选定需要替换的外观。','同一原片人物跨镜头使用固定编号；交叉和遮挡位置单独确认。','保留运动空间和遮挡关系，明确可替换与受保护的范围。','准备已保存材料，集中确认要求与本次费用，再生成并保存各段结果。','按原片时间轴检查候选，使用连续原声完成本地合成。']:legacyIntros;
 function selectSegment(id){setSegmentId(id);setMode('puppet');setCurrentTime(project?.segments?.find(s=>s.id===id)?.start||0);}
 function changeStep(n,targetId,target={}){if(target.segmentId&&project?.segments?.some(item=>item.id===target.segmentId))selectSegment(target.segmentId);setNavigationRequest({...target,step:n,targetId});setStep(n);if(n===3&&replacement)refresh(project.id).catch(setReadError);if(replacement)setMode('puppet');else if(n===0)setMode('original');else if(n===1)setMode('puppet');}
 if(loading)return <div className="loading-screen"><RefreshCw className="spin" size={25}/><h1>帧工坊</h1><p>正在打开本地工作台…</p></div>;
 const records=project&&replacement?workflowRecordLabels(project,state?.project?.id===project.id?state.generation:null,{configured:state?.config?.configured===true,selectedSegmentId:segment?.id,loading:project.id!==activeId||state?.project?.id!==project.id,hasDrafts:draftCount>0,errors:error?[error]:undefined}):[];
 const scope=stepScope(step),task=STEP_TASKS[step];
 const runningJobs=jobs.filter(j=>['QUEUED','RUNNING','PROCESSING'].includes(j.status));
 const stage=project&&(replacement?<>
  {step===0&&<GenericSourceStage project={project} onPatch={patch} onUpload={onUpload} onImport={onImport} onNext={()=>changeStep(1)} selectedId={segment?.id} onSelectSegment={selectSegment} saveError={error}/>}
  {step===1&&<PeopleStage draftTarget={navigationRequest} key={project.id} project={project} segment={segment} onPatch={patch} onUpload={onUpload} onImport={onImport} onOpenSeries={()=>setSeriesOpen(true)} onSelectSegment={selectSegment} busy={!!busy} error={error}/>}
  {step===2&&<SceneStage draftTarget={navigationRequest} key={project.id} project={project} segment={segment} onPatch={patch} onUpload={onUpload} onImport={onImport} onAction={onAction} onSelectSegment={selectSegment} busy={!!busy} error={error}/>}
  {step===3&&(project.id!==activeId||state?.project?.id!==project.id?<p role="status">正在切换项目…</p>:<ReplacementCheckStage key={project.id} project={project} segment={segment} onPatch={patch} onAction={onAction} onStep={changeStep} onSelectSegment={selectSegment} onConfig={()=>{setError('');setConfigOpen(true);}} onHistory={()=>setSupportOpen('history')} config={state?.config} busy={!!busy} error={error} jobs={jobs} generation={state?.generation} onGeneration={onGeneration} draftCount={draftCount} dirtyDraftTargets={unsavedDraftTargets(project)}/>)}
  {step===4&&(project.id!==activeId||state?.project?.id!==project.id?<p role="status">正在切换项目…</p>:<ReplacementExportStage key={project.id} resultStates={state?.generation?.result_states} onSelectSegment={selectSegment} error={error} dirtyDraftTargets={unsavedDraftTargets(project)} busy={busy} materialConfirmation={state?.generation?.material_confirmation} project={project} segment={segment} onAction={onAction} onUpload={onUpload} onImport={onImport} onStep={changeStep} jobs={jobs}/>)}
 </>:<>
  <Notice>当前是历史人偶项目；新建项目使用人物与场景替换流程。云端上传与付费生成等待确认。</Notice>
  <MediaPreview project={project} segment={segment} mode={mode} setMode={setMode} onTime={setCurrentTime}/>
  <SegmentList project={project} segments={project.segments||[]} selectedId={segment?.id} onSelect={selectSegment}/>
  {step===0&&<SourceStage project={project} onPatch={patch} onUpload={onUpload} onImport={onImport}/>}
  {step===1&&<PuppetStage project={project} segment={segment} onPatch={patch} onAction={onAction} onUpload={onUpload} onImport={onImport}/>}
  {step===2&&<AssetsStage project={project} onPatch={patch} onUpload={onUpload} onImport={onImport} onAction={onAction}/>}
  {step===3&&<GenerateStage project={project} segment={segment} onPatch={patch} onAction={onAction} onUpload={onUpload} jobs={jobs}/>}
  {step===4&&<MusicStage project={project} segment={segment} onPatch={patch} onAction={onAction} onUpload={onUpload} onImport={onImport}/>}
 </>);
 return <div className={`app-shell ${replacement?'replacement-workspace':''} ${focusedTask?'source-workspace':''} ${peopleTask?'people-workspace':''} ${sceneTask?'scenes-workspace':''} ${materialTask?'generation-workspace':''} ${resultTask?'result-workspace':''}`}>
  {saveConflict&&<dialog open className="draft-conflict-dialog" aria-labelledby="draft-conflict-title"><h2 id="draft-conflict-title">项目已更新，请选择冲突内容</h2><p>你的草稿仍保留。未冲突的修改和最新材料会一起保存；以下内容需逐项选择。</p>{saveConflict.conflicts.map(conflict=><fieldset key={conflict.key}><legend>{conflict.label||'修改内容'}</legend>{[['mine','保留我的修改',conflict.mine],['latest','使用最新内容',conflict.latest]].map(([choice,label,value])=><label key={choice}><input type="radio" name={conflict.key} checked={conflictChoices[conflict.key]===choice} onChange={()=>setConflictChoices(current=>({...current,[conflict.key]:choice}))}/><span>{label}<pre>{value===undefined?'删除此记录':typeof value==='string'?value:JSON.stringify(value,null,2)}</pre></span></label>)}</fieldset>)}<div className="button-row"><Button onClick={()=>{saveConflict.resolve(null);setSaveConflict(null);}}>返回编辑，保留草稿</Button><Button variant="primary" disabled={saveConflict.conflicts.some(conflict=>!conflictChoices[conflict.key])} onClick={()=>{saveConflict.resolve(conflictChoices);setSaveConflict(null);}}>按所选内容保存</Button></div></dialog>}
  <header className="app-header">
   <a href="/" className="brand" aria-label="帧工坊工作台">帧工坊{replacement&&<small>视频人物与场景替换</small>}</a>
   <span className="header-divider"/>
   <div className="project-switch"><span>{focusedTask&&project?.series_link?`第 ${project.series_link.episode_number} 集`:'当前项目'}</span><select value={activeId||''} onChange={e=>selectProject(e.target.value)} aria-label="当前项目">{state?.projects?.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}{!state?.projects?.length&&<option value="">尚无项目</option>}</select><ChevronDown size={15}/></div>
   <Button onClick={()=>setSeriesOpen(true)} aria-label="打开剧集与全剧角色库" data-testid="open-series-workspace"><FolderOpen size={16}/>剧集与角色库</Button>
   <Button onClick={()=>setNewOpen(true)}><Plus size={16}/>新建项目</Button>
  </header>
  <div className="app-body">
   <nav className="workflow" aria-label="制作流程">
    {replacement&&<div className="workflow-caption">制作流程</div>}
    <div className="workflow-steps">{steps.map(([name,desc],i)=><button data-testid={`workflow-step-${i}`} key={name} className={`workflow-step ${step===i?'active':''}`} onClick={()=>changeStep(i)} aria-current={step===i?'step':undefined}><span className="step-number">{String(i+1).padStart(2,'0')}</span><span className="step-name"><strong>{replacement?['原片与分段','人物替换','场景与范围','生成视频','结果与导出'][i]:name}</strong>{!replacement&&<small>{desc}</small>}{replacement&&i===step&&!focusedTask&&<span className="step-record">{records[i]}</span>}</span></button>)}</div>
    <button className="api-nav" onClick={()=>{setError('');setConfigOpen(true);}}><Settings size={replacement?18:22}/><span><strong>API 设置</strong>{!replacement&&<small>{state?.config?.configured?'百炼密钥已配置':'本地准备可先完成'}</small>}</span><ChevronRight size={17}/></button>
   </nav>
   <div className="workspace-layout">
    <main className="workspace">
     {replacement&&project&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<section className="project-context" aria-label="当前项目与制作目标">
      <div><span className="context-label">当前项目{project.series_link?` · 第 ${project.series_link.episode_number} 集`:' · 独立视频'}</span><strong>{project.name}</strong></div>
      <p>制作目标：替换选定人物与场景，保留原片动作、镜头、节奏和原声。</p>
      <div className="project-context-meta"><span>原片：{project.source?.name||'尚未导入'}</span>{project.source&&<span>{time(project.source_meta?.duration||0)} · {project.segments?.length||0} 个片段 · {project.source_actors?.length||0} 名人物</span>}<span className="local-only-state">结果复核与导出在本机完成</span></div>
     </section>}
     <div className="workspace-heading" data-testid="workspace-heading" ref={heading} tabIndex="-1">
      <div>{replacement&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<span className="context-label">第 {step+1} 步，共 5 步</span>}<h1>{sourceTask?(project?.source?'确认分段':'导入原片'):peopleTask?'人物替换':sceneTask?'场景与范围':materialTask?'生成视频':steps[step][0]}</h1>{!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<p>{replacement?task[0]:intros[step]}</p>}</div>
      <button className="icon-button" aria-label="刷新项目状态" onClick={()=>safe(()=>refresh(),'refresh')}><RefreshCw size={18} className={busy==='refresh'?'spin':''}/></button>
     </div>
     {readError&&<div className="alert-banner" role="status"><AlertCircle size={18}/><div><strong>项目状态暂未更新</strong><p>{readError.message}</p><small>当前内容与未保存修改仍保留；这里只重新读取状态，不重发保存、上传或生成请求。</small></div><Button onClick={()=>refresh(activeIdRef.current||initial.current.projectId).catch(setReadError)}>重新读取状态</Button></div>}
     {error&&!materialTask&&!resultTask&&<div className="alert-banner" role="alert"><AlertCircle size={18}/><div><strong>本次操作未完成</strong><p>{error.includes("No such file or directory")?"找不到本机文件。请检查文件路径和名称，确认文件仍在原位置。":error}</p><small>请按提示修正后重试。已保存的材料可继续在本机查看。</small></div><button className="icon-button" onClick={()=>setError('')} aria-label="关闭错误提示"><X size={16}/></button></div>}
     {project?<>
      {replacement&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<div className="task-context"><div><strong>{scope[0]}</strong><p>{scope[1]}</p></div><div><span>本步产出</span><strong>{task[1]}</strong></div></div>}
      {!materialTask&&!resultTask&&draftCount>0&&(!sourceTask||draftCount>unsavedDraftCount(project.id+'segments'))&&(peopleTask?<p className="people-global-draft" role="status">有 {draftCount} 处未保存修改，请在对应人物或角色中保存。</p>:sceneTask?<p className="scene-global-draft" role="status">有 {draftCount} 处修改待记录或保存，请查看对应编辑区域。</p>:materialTask?<p className="material-global-draft" role="status">有 {draftCount} 处未保存修改，请先保存后再生成。</p>:<Notice>有 {draftCount} 处未保存修改，尚未进入材料与审核清单。切换步骤可继续编辑；刷新或关闭页面前请先保存。</Notice>)}
      {!materialTask&&!resultTask&&runningJobs.length>0&&<div className="processing-feedback" role="status" aria-live="polite"><RefreshCw size={17} className="spin"/><div><strong>{actionName(runningJobs[0].action)} · {runningJobs[0].status==='QUEUED'?'等待本机处理':'正在本机处理'}</strong><span>{runningJobs[0].message||(focusedTask||sceneTask||materialTask?'完成后自动更新，可在“处理记录”查看详情。':'完成后会更新项目状态，详细记录在本页下方。')}</span></div></div>}
      {replacement&&step>=2&&!sceneTask&&!materialTask&&!resultTask&&project.segments?.length>0&&<section className="task-segment-picker" aria-label="选择当前处理片段"><div className="section-top"><div><h2>当前处理片段</h2><p>{step===2?'每个片段分别保存替换与保护范围。':step===3?'切换片段，核对各自的参考图与提示词。':'切换片段，核对自动保存或导入的生成结果。'}</p></div></div><SegmentList compact project={project} segments={project.segments} selectedId={segment?.id} onSelect={selectSegment}/></section>}
      <div className="current-task-content">{stage}</div>
      {replacement&&!sourceTask&&<>
       <footer className="step-navigation" aria-label="当前步骤与下一步">{!materialTask&&!resultTask&&<div className="save-state" role="status"><strong>{draftCount?sceneTask?'有修改待记录或保存':'有未保存修改':peopleTask?'当前显示已保存的人物记录':sceneTask?'当前显示已保存的场景与片段记录':materialTask?'生成任务与已保存材料分别记录':'当前显示已保存的项目记录'}</strong>{((!peopleTask&&!sceneTask&&!materialTask&&!resultTask)||draftCount>0)&&<small>{draftCount?'请使用对应区域的保存按钮。跳转不会自动保存。':'修改字段后，使用对应区域的保存按钮写入本机。'}</small>}</div>}<div className="button-row">{step>0&&!resultTask&&<Button onClick={()=>changeStep(step-1)}>上一步</Button>}{step<4&&!materialTask&&<Button variant="primary" onClick={()=>changeStep(step+1)}>{peopleTask?'下一步：场景与范围':sceneTask?'下一步：生成视频':materialTask?'下一步：结果与导出':`前往下一步：${steps[step+1][0]}`}</Button>}{step===4&&<Button onClick={()=>changeStep(3)}>返回生成视频</Button>}</div></footer>
       {!peopleTask&&!sceneTask&&!materialTask&&!resultTask&&<details className="workspace-support workspace-reference" open={previewOpen} onToggle={e=>setPreviewOpen(e.currentTarget.open)}><summary><span><Video size={17}/>查看原片与分段视频</span><small>用于浏览和对照，可放大预览</small></summary>{previewOpen&&<div className="workspace-support-body"><SegmentList project={project} segments={project.segments||[]} selectedId={segment?.id} onSelect={selectSegment}/><MediaPreview compact project={project} segment={segment} mode={mode} setMode={setMode} onTime={setCurrentTime}/></div>}</details>}
       {!peopleTask&&!sceneTask&&!materialTask&&!resultTask&&<details className="workspace-support"><summary><span>查看原片时间轴与声音波形</span><small>分段顺序与重叠关系</small></summary><div className="workspace-support-body"><Timeline project={project} selectedId={segment?.id} onSelect={selectSegment} currentTime={currentTime}/></div></details>}
      </>}
      {!replacement&&<Timeline project={project} selectedId={segment?.id} onSelect={selectSegment} currentTime={currentTime}/>}
      {!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&<JobList project={project} jobs={jobs}/>}
      {(focusedTask||sceneTask||materialTask||resultTask)&&<>
       {!materialTask&&!resultTask&&['FAILED','UNKNOWN'].includes(jobs[0]?.status)&&<Notice tone={jobs[0].status==='FAILED'?'error':'info'}>{actionName(jobs[0].action)}：{jobs[0].message||'请查看处理记录，确认结果后重试。'}<button className="text-button" onClick={()=>setSupportOpen('history')}>查看处理记录</button></Notice>}
       <nav className="task-tools" aria-label={peopleTask?'人物替换辅助工具':sceneTask?'场景与范围辅助工具':materialTask?'生成视频辅助工具':resultTask?'结果与导出辅助工具':'原片与分段辅助工具'}>{(peopleTask||sceneTask||materialTask||resultTask)&&<button className="text-button" onClick={()=>setSupportOpen('reference')}><Video size={15}/>原片与时间轴</button>}<button className="text-button" onClick={()=>setSupportOpen('history')}>处理记录</button><button className="text-button" onClick={()=>setSupportOpen('help')}>使用说明</button>{!materialTask&&!resultTask&&<span className="local-only-state">{materialTask?'确认费用后调用 API':'仅本地处理'}</span>}</nav>
      </>}
     </>:<Empty><span>替换原视频中的人物或场景，从新建项目、导入原片开始。</span><Button variant="primary" onClick={()=>setNewOpen(true)}><Plus size={16}/>新建项目并开始</Button></Empty>}
    </main>
    {project&&!focusedTask&&!sceneTask&&!materialTask&&!resultTask&&(replacement?<ReplacementInspector step={step} project={project} segment={segment} onPatch={patch} onAction={onAction} onStep={changeStep} jobs={jobs} busy={busy} config={state?.config} onConfig={()=>{setError('');setConfigOpen(true);}}/>:<Inspector project={project} segment={segment} onPatch={patch} onAction={onAction} onStep={changeStep} jobs={jobs} busy={busy} config={state?.config} onConfig={()=>{setError('');setConfigOpen(true);}}/>)}
   </div>
  </div>
  {(focusedTask||sceneTask||materialTask||resultTask)&&supportOpen&&<WorkspaceSupport title={{reference:'原片与时间轴',history:'处理记录',help:'使用说明'}[supportOpen]} onClose={()=>setSupportOpen(null)}>
   {(peopleTask||sceneTask||materialTask||resultTask)&&supportOpen==='reference'&&<><SegmentList project={project} segments={project.segments||[]} selectedId={segment?.id} onSelect={selectSegment}/><MediaPreview compact project={project} segment={segment} mode={mode} setMode={setMode} onTime={setCurrentTime}/><Timeline project={project} selectedId={segment?.id} onSelect={selectSegment} currentTime={currentTime}/></>}
   {supportOpen==='history'&&<JobList project={project} jobs={jobs}/>}
   {supportOpen==='help'&&sourceTask&&<div className="source-help"><h3>制作目标与保留要求</h3><p>替换选定人物与场景，保留原片动作、镜头、构图、节奏和原声。人物档案用于全片，替换范围与生成结果按片段处理。</p><p>动作、镜头、节奏和原声作为后续验收依据，不通过改变速度来对齐。最终帧率沿用原片（{project.source_meta?.fps_rational||project.source_meta?.fps||'随原片'}），最终成片接回一次连续原声音轨，水印关闭。生成结果仍须逐段对照检查。</p><h3>分段与保存</h3><p>每段大于 0 秒且不超过 15 秒，起止时间须在原片范围内。分段按原片顺序处理；导出时去除重叠，并接回同一条原声。时间默认显示到毫秒，编辑时显示原始精度；未修改的时间不会被四舍五入保存。</p><p>原片和分段范围保存在本机。修改后需点击“保存分段范围”；“下一步”仅切换步骤，不会自动保存。刷新或关闭页面前请先保存。</p><ReplacementWorkflowGuide/><h3>本地处理与云端生成</h3><p>本步仅在本机整理原片与分段。第 4 步准备材料后，仍需逐项核对要求并确认本次费用，才会上传和付费生成。配置 API Key 只更新连接设置。</p></div>}
   {supportOpen==='help'&&peopleTask&&<div className="source-help"><h3>人物对应与替换方式</h3><p>同一原片人物跨镜头沿用同一固定编号，不因左右位置、转身、遮挡或再次入画而更换身份。角色参考图提供外观；只换头部保留身体和服装，整个人物外观包含新造型服装，保持原样不会清除已有绑定或定位。</p><h3>位置核对</h3><p>已选择替换角色与已确认出现位置是不同状态。每个片段分别记录此人的可见、遮挡或出画状态；多人交叉、遮挡前后和跨镜头处需逐条核对。定位框只用于辨认身份，不是逐像素替换遮罩，也不代表动作或自动跟踪通过。</p><h3>保存与角色复用</h3><p>人物保存包含对应、替换方式、辨认资料与全部片段的位置记录。勾选确认后仍需保存人物。角色名称与外观说明独立保存，图片导入和移除即时生效；多个人物可引用同一份角色资料，材料检查仍会核对重复对应和模型参考图数量限制。</p><p>切换人物、关闭编辑或切换步骤不会自动保存，当前会话草稿仍保留；刷新或关闭页面前请先保存。下一步只导航，待处理状态不代表材料检查或全剧身份审核通过。普通人物保存不能确认全剧身份；固定素材版本仍在“剧集与角色库”管理。</p><h3>原片保留要求</h3><p>保留原片动作、镜头、构图、节奏和连续原声，不用改变速度对齐。最终帧率沿用原片，水印关闭；生成结果仍需逐段验收。本步在本机整理材料，付费生成需在第 4 步确认要求和本次费用。</p></div>}
   {supportOpen==='help'&&sceneTask&&<div className="source-help"><h3>场景与片段范围</h3><p>场景方式、外观说明、场景定位与参考图用于全片。替换和保护遮罩、轮廓关键帧及检查预览属于选中的片段。保留原场景时，待替换人物仍需编辑遮罩；第 2 步的定位框只用于认人。</p><h3>替换与前景保护</h3><p>编辑遮罩白色允许改变外观、黑色保留原片；前景保护遮罩白色强制恢复原片前景。手、武器、球拍等物体经过人物前方时，应仔细核对保护区域。保护文件可选，编辑文件不能全黑。</p><h3>轮廓记录与保存</h3><p>在画面暂停后沿边缘依次点击至少 3 点，记录到当前帧，再核对并保存此片段的关键帧。记录、保存和制作遮罩是独立操作；不会自动记录或保存。切换片段、制作方式或步骤时保留当前会话草稿，刷新或关闭页面前请先保存。</p><p>同一轮廓跨帧沿用固定编号、人物、用途、顶点数量和点序。片段首末帧需要记录；换镜头、交叉、遮挡、出画和形状变化处须连续帧校正，不能跨断点插值。复制的轮廓仍待校正与确认。保存允许保留阶段性草稿，制作时按现有规则检查几何与插值完整性。</p><h3>已有文件与检查预览</h3><p>可以直接导入已有逐帧遮罩，无需另画关键帧。导入即保存并替换当前片段的对应遮罩文件，不清除已有关键帧或上次制作预览。按已保存关键帧制作遮罩时，会更新此片段的编辑、保护文件和检查预览；原文件仍保存在本机。</p><p>遮罩尺寸、帧率、实际帧数和时间范围须与片段一致，逐帧像素必须是纯 0 或 255。文件存在不代表技术检查通过；完整校验仍在材料检查及局部合成时执行。检查预览是原片叠加区域，橙色代表替换、蓝色代表保护，仍需人工检查边缘、身份和遮挡，不是角色替换结果。</p><h3>原片保留要求</h3><p>保留原片动作、机位、运动空间、构图、节奏和连续原声，沿用原片帧率，不用调速对齐。下一步仅导航，不自动保存；已有校验和材料审核规则保持不变。本步在本机整理材料，付费生成需在第 4 步确认要求和本次费用。</p></div>}
   {supportOpen==='help'&&resultTask&&<div className="source-help"><h3>接收与处理</h3><p>API 结果自动接入对应片段；平台结果需导入当前段。检查并合成会先核对尺寸、帧率、帧数和时长，通过后才按替换与保护范围合成。</p><h3>对照验收</h3><p>同步播放原片与合成预览，逐项核对六项实际效果。勾选尚未保存，保存成功后才计入全片进度。重新检查、重新合成或更换候选可能使原验收失效，执行前会再次确认。</p><h3>完整成片</h3><p>所有片段满足当前版本材料确认、严格技术检查、区域合成和六项验收后才能导出。成片按原片时间轴拼接并接回连续原声；观看版用于播放，无损母版可在文件详情下载。</p></div>}
   {supportOpen==='help'&&materialTask&&<div className="source-help"><h3>准备与确认</h3><p>准备生成会自动检查已保存材料、制作分段并估算费用。需要时确认人物对应、参考图及替换与保护范围，再单独授权本次上传与付费；当前版本已有有效材料确认时直接复用。</p><h3>平台备用路径</h3><p>未配置API Key时可直接导出生成包，到平台生成后在第5步导入对应片段。待确认包仍可导出；返回本页勾选材料确认后，沿原导出操作补齐记录即可继续验收，不会调用生成API。</p><h3>草稿与版本</h3><p>提示词只保存当前片段，切换片段或步骤保留会话草稿。按材料重建会覆盖并保存本段提示词，仍需明确确认。材料、提示词和设置变化后，按版本重新准备与确认；历史任务及文件保留来源。</p><h3>技术检查与结果验收</h3><p>收费前检查API输入要求、输出档位与本工具严格合成限制，不能以人工确认绕过。比例随原片不保证精确宽高；实际结果仍须在第5步检查尺寸、帧率、帧数、时长，并逐项验收人物、动作和遮挡。保留原声，无水印，不自动调速、补帧、缩放或裁剪。</p></div>}
  </WorkspaceSupport>}
  {seriesOpen&&<SeriesWorkspace project={project} onProjectRefresh={async()=>{const result=await refresh(activeIdRef.current);return result.project;}} onSelectProject={async id=>{setSeriesOpen(false);await selectProject(id);}} onClose={()=>setSeriesOpen(false)}/>}
  {toast&&<div className="toast" role="status">{toast}</div>}
  {configOpen&&<ConfigDrawer error={error} config={state?.config} system={state?.system} onClose={()=>setConfigOpen(false)} onSave={config=>safe(async()=>{await request('/api/config',{method:'POST',body:config});await refresh();setToast('API 连接配置已保存，尚未验证权限');},'config')}/>}
  {newOpen&&<div className="modal-backdrop" onKeyDown={e=>{if(e.key==='Escape')setNewOpen(false);}} onMouseDown={e=>{if(e.target===e.currentTarget)setNewOpen(false);}}><form className="new-project-modal" role="dialog" aria-modal="true" aria-labelledby="new-project-title" onSubmit={create}><div className="modal-top"><h2 id="new-project-title">新建视频项目</h2><button type="button" className="icon-button" onClick={()=>setNewOpen(false)} aria-label="关闭新建项目"><X size={18}/></button></div>{error&&<Notice tone="error">{error}</Notice>}<p>一个项目用于处理一条原视频。创建后，先导入原片，再指定需要替换的人物和场景。</p><label className="field"><span>项目名称</span><input autoFocus value={newName} onChange={e=>setNewName(e.target.value)} placeholder="例如：第 1 集人物替换"/><small>留空将使用“未命名项目”。也可独立制作视频，无需建立剧集。</small></label><p>原片材料、人物对应和处理记录保存在本机。第 4 步确认材料与本次费用后，才会上传并调用生成 API。</p><Button type="submit" variant="primary" busy={busy==='create'}>创建项目，开始导入原片</Button></form></div>}
 </div>;
}

function WorkspaceSupport({title,onClose,children}){
 const dialog=useRef(null);
 useEffect(()=>{const element=dialog.current;element.showModal();return()=>element.close();},[]);
 return <dialog ref={dialog} className="support-dialog" aria-labelledby="support-dialog-title" onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget)onClose();}}><div className="support-dialog-inner"><div className="support-dialog-heading"><h2 id="support-dialog-title">{title}</h2><button autoFocus className="icon-button" onClick={onClose} aria-label={`关闭${title}`}><X size={20}/></button></div><div className="support-dialog-body">{children}</div></div></dialog>;
}
