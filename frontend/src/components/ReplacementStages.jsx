import {Collapsible as CollapsibleRoot,CollapsibleTrigger,CollapsibleContent} from "./ui/collapsible";
import {Collapsible as UiCollapsible,CollapsibleTrigger as UiCollapsibleTrigger,CollapsibleContent as UiCollapsibleContent} from "./ui/collapsible";
import {Empty,EmptyHeader,EmptyMedia,EmptyTitle,EmptyDescription} from "./ui/empty";
import {cn} from "@/lib/utils";
import {Spinner} from "./ui/spinner";
import {Field,FieldLabel} from "./ui/field";
import React, {useEffect,useId,useRef,useState} from "react";
import {Button as UiButton} from "./ui/button";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from './ui/card';
import { Badge } from './ui/badge';
import { Separator } from './ui/separator';
import {Alert,AlertDescription} from "./ui/alert";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from './ui/select';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from './ui/table';
import { NativeSelect } from './ui/native-select';
import {createPortal} from 'react-dom';


import {Plus,Save,Trash2,ScanEye,Package,Play,Download,ShieldCheck,Users,MapPin,ArrowLeft,ArrowRight,ChevronDown,AlertCircle,Images,PanelRightClose,PanelRightOpen} from "lucide-react";
import {assetUrl,time} from '../api';

import PeopleRoleManager from './PeopleRoleManager';
import PeoplePositionReview from './PeoplePositionReview';
import SceneSettings from './SceneSettings';
import SceneMaskEditor,{importedMaskDraftTargets} from './SceneMaskEditor';
import SceneMaskPanel from './SceneMaskPanel';
import MaterialStage from './MaterialStage';
import GenerationStage from './GenerationStage';
import {GenerationDisclosure} from './GenerationControls';
import ResultStage from './ResultStage';
import './SceneStage.css';
import './SceneMaskEditor.css';
import {actorPresentation,groupActorObservations} from '../peoplePresentation';
import useDraft from '../useDraft';
import {DRAFTS,draftChanged} from '../draftState';
import './PeopleStage.css';
import './SeriesWorkspace.css';
import './ReplacementStages.css';

const clone=v=>JSON.parse(JSON.stringify(v));
const makeId=prefix=>`${prefix}_${crypto.randomUUID().replaceAll('-','').slice(0,12)}`;
const COLORS=['#c28e3c','#7399ba','#7a8b75','#9d789f','#c27c66','#6c9e9e'];
const CHECKS=[['identity','人物身份、目标角色和跨镜头对应准确'],['action','表情、可见口型、动作、持物手、接触时点和身体朝向与原片一致'],['camera','原片机位、运镜、构图和遮挡顺序保持'],['timing','帧率、片段范围、镜头节奏与原片保持'],['audio','沿用原片连续声音，不新增声音或改变卡点'],['occlusion','交叉、遮挡、出画与再入画的位置已逐处检查']];

// Drafts survive step/project navigation within this browser session.
const MASK_WORK=new Map();
function rememberMaskWorking(identity,value,dirty){
 const serialized=JSON.stringify(value),previous=MASK_WORK.get(identity);
 if(previous&&JSON.stringify(previous)===serialized&&DRAFTS.has(identity)===dirty)return;
 MASK_WORK.set(identity,clone(value));
 if(dirty)DRAFTS.set(identity,{value:clone(value),working:true});
 else if(DRAFTS.get(identity)?.working)DRAFTS.delete(identity);
 draftChanged();
}
export function unsavedDraftCount(projectId=''){return [...DRAFTS.keys()].filter(key=>key.startsWith(projectId)).length;}
export function unsavedDraftTargets(project){
 if(!project)return [];
 const pid=project.id,targets=[];
 const add=(identity,target)=>{if(DRAFTS.has(identity))targets.push(target);};
 add(pid+'segments',{label:'原片分段',step:0,targetId:'source-segments'});
 for(const actor of project.source_actors||[])add(pid+actor.id,{label:`人物：${DRAFTS.get(pid+actor.id)?.value?.label||actor.label}`,step:1,targetId:`actor-${actor.id}`,actorId:actor.id});
 for(const role of project.roles||[])add(pid+role.id,{label:`角色：${DRAFTS.get(pid+role.id)?.value?.name||role.name}`,step:1,targetId:'identity-mapping',roleId:role.id});
 add(pid+'scene',{label:'全片场景',step:2,targetId:'scene-settings'});
 for(const segment of project.segments||[]){
  if(DRAFTS.has(pid+'mask'+segment.id)||DRAFTS.has(pid+'mask-work'+segment.id))targets.push({label:`${segment.name||segment.id}的替换与保护范围`,step:2,targetId:'mask-panel',segmentId:segment.id,maskEditor:true});
  add(pid+segment.id,{label:`${segment.name||segment.id}的提示词`,step:3,targetId:'prompt-editor',segmentId:segment.id});
 }
 targets.push(...importedMaskDraftTargets(project));
 return targets;
}
function DraftNotice({dirty,onDiscard}){return dirty?<Alert tone="warning" role="status"><AlertDescription><p>有未保存的修改。请保存后再制作材料；关闭或刷新页面会丢失这些修改。</p>{onDiscard&&<UiButton type="button" onClick={onDiscard} variant={"link"} size={"default"}>放弃未保存修改</UiButton>}</AlertDescription></Alert>:null;}

function ActionHint({children}){return children?<p className="action-hint">{children}</p>:null;}
function AssetLocation({asset}){return asset?<UiCollapsible className="asset-location group/disclosure"><UiCollapsibleTrigger asChild><UiButton type="button" variant="ghost" className="h-auto w-full justify-between whitespace-normal py-2 text-left">文件名称与本机位置<ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4"><strong>{asset.name}</strong><code>{asset.path||'文件保存在本机项目中，可使用上方链接查看或下载。'}</code></UiCollapsibleContent></UiCollapsible>:null;}
function ScopeNote({children}){return <p className="scope-note">{children}</p>;}

function ActorPortrait({project,actor,as:Frame='div'}){
 const canvas=useRef(),video=useRef();
 const observation=actor.observations?.find(o=>o.visibility==='visible'&&o.confirmed)||actor.observations?.find(o=>o.visibility==='visible');
 const [loaded,setLoaded]=useState(false),[failed,setFailed]=useState(false);
 useEffect(()=>{setLoaded(false);setFailed(false);const v=video.current;if(v&&observation&&v.readyState>=1){const target=observation.time+Number(project.source_meta?.video_start_time||0);if(Math.abs(v.currentTime-target)<.001)draw();else v.currentTime=target;}},[project.source?.id,observation?.time,JSON.stringify(observation?.box)]);
 function draw(){const v=video.current,c=canvas.current;if(!v||!c||!v.videoWidth||v.seeking)return;const b=observation?.box||[0,0,1,1],w=Math.max(1,(b[2]-b[0])*v.videoWidth),h=Math.max(1,(b[3]-b[1])*v.videoHeight);const scale=Math.min(c.width/w,c.height/h),ctx=c.getContext('2d');ctx.fillStyle=getComputedStyle(c).getPropertyValue('--media-background').trim();ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(v,b[0]*v.videoWidth,b[1]*v.videoHeight,w,h,(c.width-w*scale)/2,(c.height-h*scale)/2,w*scale,h*scale);setLoaded(true);}
 return <Frame className="actor-portrait" aria-label={`${actor.label}原片定位截图`}><canvas ref={canvas} width="180" height="210" hidden={!loaded}/>{!loaded&&<span>{failed?'打开人物详情查看原片':observation?'读取原片截图…':'添加人物出现位置后显示截图'}</span>}{observation&&project.source&&<video ref={video} src={assetUrl(project.source)} muted playsInline preload="metadata" hidden onLoadedMetadata={()=>{video.current.currentTime=observation.time+Number(project.source_meta?.video_start_time||0);}} onSeeked={draw} onLoadedData={draw} onError={()=>setFailed(true)}/>}</Frame>;
}

export function ReplacementWorkflowGuide({Disclosure}={}){
 const label='什么情况下才需要白模视频？';
 const explanation=<p>当你主动设计新的动作、机位、走位，或没有可用的运动参考时，可先做白模预演并审核。白模不是身份绑定工具；剪影、简化肢体或错误姿态也会影响后续生成。需要保留原武打动作时，优先沿用原片与局部替换，白模保持可选。</p>;
 return <section className="replacement-path" aria-label="原片替换工作流"><strong>原片 → 人物与场景 → 范围与审核 → Wan 编辑 → 原声导出</strong><p>当前流程直接使用原片分段，无需先做白模。原片提供动作、运镜和节奏；图片提供新角色或场景的外观。</p>{Disclosure?<Disclosure label={label}>{explanation}</Disclosure>:<UiCollapsible className="group/disclosure"><UiCollapsibleTrigger asChild><UiButton type="button" variant="ghost" className="h-auto w-full justify-between whitespace-normal py-2 text-left">{label}<ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4">{explanation}</UiCollapsibleContent></UiCollapsible>}</section>;
}

export {GenericSourceStage} from './SourceWorkspace';

export function PeopleStage({ project, segment, onPatch, onUpload, onImport, onOpenSeries, onSelectSegment,
  onPrevious, onNext, draftCount = 0, busy = false, error, draftTarget }) {
  const [activeActor, setActiveActor] = useState(() => project.source_actors?.[0]?.id || null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorHost, setInspectorHost] = useState(null);
  const [roleManager, setRoleManager] = useState(null);
  const [roleSelection, setRoleSelection] = useState(null);
  const roleTrigger = useRef(null);
  const stageRef = useRef(null);
  const inspectorRef = useRef(null);
  const inspectorToggle = useRef(null);
  const actorCount = project.source_actors?.length || 0;

  useEffect(() => { inspectorRef.current?.scrollTo({ top: 0 }); }, [activeActor]);

  useEffect(() => {
    if (draftTarget?.step !== 1) return;
    setInspectorOpen(true);
    if (draftTarget.actorId && project.source_actors?.some(actor => actor.id === draftTarget.actorId)) setActiveActor(draftTarget.actorId);
    if (draftTarget.roleId && project.roles?.some(role => role.id === draftTarget.roleId)) {
      roleTrigger.current = stageRef.current;
      setRoleManager({ roleId: draftTarget.roleId });
    }
  }, [draftTarget, project.id]);

  function selectActor(id) {
    setActiveActor(id);
    if (id) setInspectorOpen(true);
  }

  function closeInspector() {
    if (inspectorRef.current?.contains(document.activeElement)) inspectorToggle.current?.focus({ preventScroll: true });
    setInspectorOpen(false);
  }

  async function addRole(value) {
    const role = { id: makeId('role'), name: value.trim() || `目标角色 ${project.roles.length + 1}`,
      color: COLORS[project.roles.length % COLORS.length], images: [], description: '' };
    const result = await onPatch({ roles: [...project.roles, role] });
    return result ? role.id : null;
  }

  async function addActor() {
    const actors = project.source_actors || [];
    const actor = { id: makeId('actor'), label: `原片人物 ${actors.length + 1}`, source_description: '',
      scope: 'head', target_role_id: '', color: COLORS[actors.length % COLORS.length], observations: [] };
    if (await onPatch({ source_actors: [...actors, actor] })) selectActor(actor.id);
  }

  function manageRole(event, actorId, roleId, create = false) {
    roleTrigger.current = event.currentTarget;
    setRoleManager({ actorId, roleId, create });
  }

  function chooseRole(roleId) {
    setRoleSelection({ actorId: roleManager.actorId, roleId });
    selectActor(roleManager.actorId);
  }

  return (
    <section ref={stageRef} className="people-stage" data-testid="people-stage" id="identity-mapping" tabIndex="-1" aria-labelledby="people-list-title">
      <header className="people-list-heading">
          <h2 id="people-list-title" className="text-lg font-semibold">人物对应</h2>
          <div className="flex items-center gap-3">
            <UiButton type="button" disabled={!project.source || busy} onClick={addActor} data-testid="add-source-actor" variant="outline">
              <Plus aria-hidden="true" data-icon="inline-start" />添加原片人物
            </UiButton>
            <UiButton ref={inspectorToggle} type="button" aria-label={inspectorOpen ? '收起人物设置' : '查看人物设置'} title={inspectorOpen ? '收起人物设置' : '查看人物设置'} aria-expanded={inspectorOpen} aria-controls="people-inspector" onClick={() => inspectorOpen ? closeInspector() : setInspectorOpen(true)} variant="ghost" size="icon">
              {inspectorOpen ? <PanelRightClose aria-hidden="true" /> : <PanelRightOpen aria-hidden="true" />}
            </UiButton>
          </div>
      </header>
      <div className="people-work-grid" data-inspector-open={inspectorOpen}>
      <div className="people-main">
      {actorCount > 0 ? <div className="people-cards">
        {(project.source_actors || []).map(actor => (
          <ActorEditor key={actor.id} actor={actor} project={project} segment={segment} onPatch={onPatch}
            open={activeActor === actor.id} onOpen={selectActor} onManageRole={manageRole} inspectorHost={inspectorHost}
            roleSelection={roleSelection?.actorId === actor.id ? roleSelection : null}
            onOpenSeries={onOpenSeries} onSelectSegment={onSelectSegment} busy={busy} error={error} />
        ))}
      </div> : <Card className="people-empty"><CardContent><Empty className="p-0 md:p-0"><EmptyHeader><EmptyMedia variant="icon"><Users aria-hidden="true" /></EmptyMedia><EmptyTitle><h3  className="text-lg font-semibold leading-snug tracking-tight">尚未添加原片人物</h3></EmptyTitle><EmptyDescription>{project.source ? '添加原片中的人物，再为每个人选择角色和替换方式。' : '请先在第 1 步导入原片。'}</EmptyDescription></EmptyHeader></Empty></CardContent></Card>}
      {!project.source && actorCount > 0 && <Alert data-tone={"warning"} role="note">
        <AlertCircle size={16} aria-hidden="true" />
        <AlertDescription>请先在第 1 步导入原片。</AlertDescription>
      </Alert>}

      </div>
      <aside id="people-inspector" ref={inspectorRef} className="people-inspector" hidden={!inspectorOpen} aria-label="人物属性与素材">
        <Card className="people-settings-panel">
          <CardHeader><CardTitle><h2 className="text-lg font-semibold">人物设置</h2></CardTitle></CardHeader>
          <CardContent>
        <div className="people-inspector-current" ref={setInspectorHost} />
        {!project.source_actors?.some(actor => actor.id === activeActor) && <div className="flex flex-col items-start gap-4">
          <p className="text-sm text-muted-foreground">选择人物，编辑替换设置。</p>
          <UiButton type="button" disabled={busy} onClick={manageRole} variant="outline">
            <Images aria-hidden="true" data-icon="inline-start" />角色与参考图
          </UiButton>
        </div>}
          </CardContent>
        </Card>
      </aside>
      </div>
      {(onPrevious || onNext) && <footer className="people-step-navigation" aria-label="当前步骤与下一步">
        <Separator className="people-navigation-divider" />
        {draftCount > 0 && <p role="status" className="text-sm text-warning">有未保存修改，切换步骤不会自动保存。</p>}
        <div className="flex gap-3 flex-wrap">
          {onPrevious && <UiButton type="button" onClick={onPrevious} variant="outline"><ArrowLeft aria-hidden="true" data-icon="inline-start" />上一步</UiButton>}
          {onNext && <UiButton type="button" onClick={onNext} variant={draftCount ? 'outline' : 'default'}>下一步：场景与范围<ArrowRight aria-hidden="true" data-icon="inline-end" /></UiButton>}
        </div>
      </footer>}
      {roleManager && <PeopleRoleManager project={project}
        draftRoleIds={project.roles.filter(role => DRAFTS.has(project.id + role.id)).map(role => role.id)}
        initialRoleId={roleManager.roleId} focusCreate={roleManager.create} onCreate={addRole}
        onChooseRole={roleManager.actorId ? chooseRole : undefined} onClose={() => setRoleManager(null)}
        returnFocus={roleTrigger} onPatch={onPatch} onUpload={onUpload} onImport={onImport}
        onSeries={onOpenSeries} busy={busy} error={error} />}
    </section>
  );
}

export function MappingTable({project}){
 return <div className="identity-pairs" data-testid="identity-mapping-table" aria-label="原片人物与目标角色绑定">{(project.source_actors||[]).map(a=>{const role=project.roles.find(r=>r.id===a.target_role_id),confirmed=a.observations?.filter(o=>o.confirmed).length||0,total=a.observations?.length||0;return <article className="identity-pair" key={a.id}><div className="pair-person"><ActorPortrait project={project} actor={a}/><div><small>原片人物</small><strong><span className="identity-dot" style={{background:a.color}}/>{a.label}</strong><p>{a.source_description||'请展开人物详情，填写衣着、发型和出场位置等辨认依据。'}</p></div></div><span className="pair-arrow" aria-label="替换为">→</span><div className="pair-person">{role?.images?.[0]?<img className="target-portrait" src={role.images[0].thumbnail_url||assetUrl(role.images[0])} alt={`${role.name}参考图`} loading="lazy"/>:<div className="target-portrait placeholder">{a.scope==='preserve'?'保留原片':'待添加参考图'}</div>}<div><small>{a.scope==='preserve'?'保持原样':'新角色'}</small><strong>{a.scope==='preserve'?'沿用原片':role?.name||'尚未选择'}</strong><p>{{head:'只换头部与发型，保留原身体和服装',person:'替换整个人物外观',preserve:'外观与动作沿用原片'}[a.scope]||a.scope}</p><Badge variant={total && confirmed === total ? 'success' : 'warning'}>{confirmed} / {total} 处人物位置已确认</Badge></div></div></article>;})}{!project.source_actors?.length&&<Alert data-tone={'info'} variant={('info') === "destructive" ? "destructive" : "default"} role={('info') === "destructive" ? "alert" : undefined}><AlertCircle aria-hidden="true" /><AlertDescription>尚未添加原片人物。添加后，原人物截图与新角色参考图会在这里并排显示。</AlertDescription></Alert>}</div>;
}

const PEOPLE_SCOPES = [
  ['head', '头部与发型（保留原身体）', '保留原身体和服装。'],
  ['person', '整个人物外观', '包含新造型的身体外观和服装。'],
  ['preserve', '保持原样', '外观与动作沿用原片；已有记录保留。'],
];

function PeopleActorMapping({ actor, draft, project, status, dirty, busy, update, onPatch, onManageRole, onOpenSeries }) {
  const fieldId = useId();
  const scope = PEOPLE_SCOPES.find(([key]) => key === draft.scope);
  const sharedWith = (project.source_actors || []).filter(other => other.id !== actor.id
    && other.scope !== 'preserve' && other.target_role_id && other.target_role_id === draft.target_role_id);
  const identityDirty = ['label', 'source_description', 'target_role_id', 'scope'].some(key => draft[key] !== actor[key]);

  return (
    <div className="people-binding-editor">
      <div className="people-binding-fields">
        <div className="people-field">
          <label htmlFor={`${fieldId}-role`} className="text-sm font-medium">替换角色</label>
          <Select value={status.role ? `role:${status.role.id}` : 'unassigned'} disabled={busy || status.preserve} onValueChange={value => update('target_role_id', value === 'unassigned' ? '' : value.slice('role:'.length))}>
            <SelectTrigger id={`${fieldId}-role`}><SelectValue>{status.role?.name || '请选择目标角色'}</SelectValue></SelectTrigger>
            <SelectContent position="popper"><SelectGroup>
              <SelectItem value="unassigned">请选择目标角色</SelectItem>
              {project.roles.map(role => <SelectItem key={role.id} value={`role:${role.id}`}>{role.name}</SelectItem>)}
            </SelectGroup></SelectContent>
          </Select>
        </div>
        <div className="people-field">
          <label htmlFor={`${fieldId}-scope`} className="text-sm font-medium">替换方式</label>
          <Select value={scope?.[0] || ''} disabled={busy} onValueChange={value => update('scope', value)}>
            <SelectTrigger id={`${fieldId}-scope`}><SelectValue placeholder="选择替换方式">{scope?.[1]}</SelectValue></SelectTrigger>
            <SelectContent position="popper"><SelectGroup>
              {PEOPLE_SCOPES.map(([key, name]) => <SelectItem key={key} value={key}>{name}</SelectItem>)}
            </SelectGroup></SelectContent>
          </Select>
        </div>
      </div>
      <div className="flex items-center flex-wrap gap-3">
        {!status.preserve && <>
          <UiButton type="button" onClick={event => onManageRole(event, actor.id, draft.target_role_id)} variant="outline">
            {status.role ? '管理角色与参考图' : '新建或选择角色'}
          </UiButton>
        </>}
      </div>
      {(!status.mappingSelected || status.needsReference || (!status.preserve && sharedWith.length > 0)) && <Alert data-tone={"warning"} role="note">
        <AlertCircle size={17} aria-hidden="true" />
          <AlertDescription>
          {!status.mappingSelected && <p>请为此人物选择替换角色。</p>}
          {status.needsReference && <p>{status.role.name}尚无参考图，请从上方管理入口导入。</p>}
          {!status.preserve && sharedWith.length > 0 && <p>
            此角色也被{sharedWith.map(other => other.label).join('、')}选用，请在材料检查时核对人物对应。
          </p>}
        </AlertDescription>
      </Alert>}
      {actor.series_binding && <div className="people-series-note">
        <span className="text-sm text-muted-foreground">{identityDirty ? '身份资料已修改，保存后需重新确认全剧对应。'
          : `固定素材 v${actor.series_binding.version} · ${actor.series_binding.confirmed ? '已保存本集对应确认' : '本集对应待确认'}`}</span>
        <UiButton type="button" onClick={onOpenSeries} variant={"outline"} size={"lg"}>查看全剧绑定</UiButton>
      </div>}
      <CollapsibleRoot className="people-person-details">
        <CollapsibleTrigger asChild>
          <UiButton type="button" className="people-detail-trigger h-auto w-[calc(100%+1.5rem)] -mx-3 justify-between whitespace-normal px-3 py-2 text-start has-[>svg]:px-3" variant="ghost">
            <span>人物资料{status.needsDescription ? ' · 待补辨认依据' : ''}</span>
            <ChevronDown aria-hidden="true" data-icon="inline-start" />
          </UiButton>
        </CollapsibleTrigger>
        <CollapsibleContent className="people-details-content">
          <div className="people-field">
            <label htmlFor={`${fieldId}-name`} className="text-sm font-medium">人物显示名称</label>
            <Input id={`${fieldId}-name`} value={draft.label} onChange={event => update('label', event.target.value)} />
          </div>
          <div className="people-field">
            <label htmlFor={`${fieldId}-description`} className="text-sm font-medium">原片辨认依据</label>
            <Textarea id={`${fieldId}-description`} rows={2} value={draft.source_description || ''} onChange={event => update('source_description', event.target.value)} aria-describedby={`${fieldId}-description-hint`} />
            <span id={`${fieldId}-description-hint`} className="text-sm text-muted-foreground">填写衣着、发型、持物手和出现镜头，避免仅用左右位置辨认。</span>
          </div>
          <div className="people-identifiers">
            <div className="people-field">
              <label htmlFor={`${fieldId}-color`} className="text-sm font-medium">定位标识颜色</label>
              <input type="color" id={`${fieldId}-color`} value={draft.color || '#7399ba'} onChange={event => update('color', event.target.value)} />
            </div>
            <UiButton type="button" disabled={dirty || busy} onClick={() => onPatch({ source_actors: project.source_actors.filter(other => other.id !== actor.id) })} variant={"destructive"} size={"lg"}>
              <Trash2 aria-hidden="true" data-icon="inline-start" />删除{actor.label}
            </UiButton>
          </div>
        </CollapsibleContent>
      </CollapsibleRoot>
    </div>
  );
}

function PeopleActorComparison({project,draft,status}){
 return <section className="people-appearance-comparison" aria-label="人物与参考外观对照">
  <div className="people-appearance-person">
   <h3 className="font-semibold leading-snug tracking-tight text-base">原片人物</h3>
   <ActorPortrait project={project} actor={draft}/>
  </div>
  <div className="people-appearance-person">
   <h3 className="font-semibold leading-snug tracking-tight text-base">{status.preserve?'保持原样':'替换角色参考'}</h3>
   {status.preserve?<div className="people-appearance-empty">保留原片人物外观与动作</div>:status.referenceCount?
    <div className="people-appearance-references">{status.role.images.map((image,index)=><img key={image.id||index}
     src={image.thumbnail_url||assetUrl(image)} alt={`${status.role.name}参考图 ${index+1}`} loading="lazy"/>)}</div>:
    <div className="people-appearance-empty">{status.role?'请在右侧管理参考图':'请在右侧选择替换角色'}</div>}
  </div>
 </section>;
}

function ActorEditor({actor,project,segment,onPatch,open,onOpen,onManageRole,roleSelection,onOpenSeries,onSelectSegment,busy,error,inspectorHost}){
 const [draft,set,dirty,save,reset]=useDraft(actor,project.id+actor.id,project.revision);
 const [tab,setTab]=useState('mapping'),[openObservation,setOpenObservation]=useState(null),[selectedSegment,setSelectedSegment]=useState(segment?.id||project.segments?.[0]?.id||'');
 const observationKey=observation=>observation.id||`${observation.segment_id}:${observation.time}`;
 const update=(key,value)=>set(d=>({...d,[key]:value}));
 useEffect(()=>{if(roleSelection)set(d=>({...d,target_role_id:roleSelection.roleId}));},[roleSelection]);
 useEffect(()=>{setSelectedSegment(segment?.id||project.segments?.[0]?.id||'');setOpenObservation(null);},[segment?.id,project.id]);
 const status=actorPresentation(draft,project.roles,project.segments),groups=groupActorObservations(draft,project.segments,project.source_analysis?.source_sha256===project.source?.sha256?project.source_analysis:null,project.source_meta);
 const currentGroup=groups.find(group=>group.segment.id===selectedSegment)||groups[0];
 const currentSegment=project.segments.find(s=>s.id===currentGroup?.segment.id);
 const activeIndex=(draft.observations||[]).findIndex(observation=>observationKey(observation)===openObservation);
 const image=!status.preserve&&status.role?.images?.[0];
 function selectSegment(id){setSelectedSegment(id);setOpenObservation(null);if(project.segments.some(s=>s.id===id))onSelectSegment?.(id);}
 function changeTab(value){
  if(value==='positions'&&tab!=='positions'){
   const pending=currentGroup?.rows.find(row=>row.observation.confirmed!==true);
   setOpenObservation(pending?observationKey(pending.observation):null);
  }
  setTab(value);
 }
 function updateObservation(index,value){set(d=>({...d,observations:d.observations.map((o,i)=>i===index?value:o)}));setOpenObservation(observationKey(value));if(value.segment_id!==currentGroup?.segment.id){setSelectedSegment(value.segment_id);if(project.segments.some(s=>s.id===value.segment_id))onSelectSegment?.(value.segment_id);}}
 function addObservation(){if(!currentSegment)return;const observation={id:makeId('position'),segment_id:currentSegment.id,time:currentSegment.start,box:[.2,.1,.8,.9],visibility:'visible',confirmed:false};set(d=>({...d,observations:[...(d.observations||[]),observation]}));setOpenObservation(observationKey(observation));}
 function selectObservation(observation){const key=observationKey(observation);setOpenObservation(current=>current===key?null:key);}
 function removeObservation(index){const key=observationKey(draft.observations[index]);set(d=>({...d,observations:d.observations.filter((_,i)=>i!==index)}));if(openObservation===key)setOpenObservation(null);}
 const positionPending=status.pending||status.missingSegments.length||!status.total;
 const positionSummary=status.total
  ? `位置 ${status.confirmed}/${status.total} 已确认${status.pending?` · ${status.pending} 待确认`:''}${status.missingSegments.length?` · ${status.missingSegments.length} 段缺记录`:''}`
  : '待添加位置记录';

 return <><CollapsibleRoot className="people-actor-group" id={`actor-${actor.id}`} data-testid={`actor-${actor.id}`} tabIndex={-1}
  open={open} onOpenChange={expanded => onOpen(expanded ? actor.id : null)}>
  <Card className="people-actor-card">
  <CardHeader className="gap-0">
  <CollapsibleTrigger asChild>
   <UiButton type="button" className="people-actor-summary h-auto w-[calc(100%+1.5rem)] -mx-3 whitespace-normal px-3 py-2 text-start has-[>svg]:px-3" aria-label={`${open ? '收起编辑' : '编辑人物'}：${draft.label || actor.label}`} variant={"ghost"} size={"lg"}>
    <span className="people-actor-person">
     {!open && (draft.observations?.some(observation => observation.visibility === 'visible')
      ? <ActorPortrait project={project} actor={draft} as="span" />
      : <span className="people-target-placeholder"><Users aria-hidden="true" /></span>)}
     <span className="people-actor-copy">
      {!open && <span className="text-sm text-muted-foreground">原片人物</span>}
      <span className="text-base font-medium">{draft.label || actor.label}</span>
      {dirty && <Badge variant="warning">未保存</Badge>}
     </span>
    </span>
    <ArrowRight className="people-pair-arrow" aria-hidden="true" data-icon="inline-start" />
    <span className="people-actor-person">
     {!open && (image ? <img className="people-actor-image" src={image.thumbnail_url || assetUrl(image)} alt={`${status.role.name}参考图`} loading="lazy" />
      : <span className="people-target-placeholder"><Images aria-hidden="true" /></span>)}
     <span className="people-actor-copy">
      {!open && <span className="text-sm text-muted-foreground">{status.preserve ? '保留原片' : '替换角色'}</span>}
      <span className="text-base font-medium">{status.preserve ? '保持原样' : status.role?.name || '尚未选择角色'}</span>
      {!open && !status.preserve && <span className="text-sm text-muted-foreground">{status.scopeLabel}</span>}
      <Badge className="people-position-status" variant={positionPending ? 'warning' : 'secondary'}>{positionSummary}</Badge>
     </span>
    </span>
    <span className="people-edit-affordance">
     <ChevronDown aria-hidden="true" />
    </span>
   </UiButton>
  </CollapsibleTrigger>
  </CardHeader>
  <CollapsibleContent className="people-actor-body">
   <CardContent>
   <fieldset className="people-editor-fieldset" disabled={busy}>
    <Tabs value={tab} onValueChange={changeTab}>
     <TabsList className="people-editor-tabs" aria-label={`${actor.label}编辑内容`}>
      <TabsTrigger value="mapping" disabled={busy}>人物对照</TabsTrigger>
      <TabsTrigger value="positions" disabled={busy}>出现位置{status.pending ? ` · ${status.pending} 待确认` : ''}</TabsTrigger>
     </TabsList>
     <TabsContent value="mapping">
      <PeopleActorComparison project={project} draft={draft} status={status}/>
     </TabsContent>
     <TabsContent value="positions" className="people-position-review">
      <PeoplePositionReview project={project} actor={draft} actorLabel={actor.label} currentGroup={currentGroup} groups={groups}
       canAdd={!!currentSegment} activeIndex={activeIndex} busy={busy} onSelectSegment={selectSegment} onAdd={addObservation}
       onSelectObservation={selectObservation} onRemoveObservation={removeObservation} onUpdateObservation={updateObservation} />
     </TabsContent>
    </Tabs>
   </fieldset>
   </CardContent>
  </CollapsibleContent>
  </Card>
 </CollapsibleRoot>
 {open&&inspectorHost&&createPortal(<section className="people-current-actor" aria-label={`${draft.label||actor.label}设置`}>
   <fieldset className="people-editor-fieldset" disabled={busy}>
    <legend className="sr-only">{draft.label||actor.label}</legend>
    <PeopleActorMapping actor={actor} draft={draft} project={project} status={status} dirty={dirty} busy={busy}
     update={update} onPatch={onPatch} onManageRole={onManageRole} onOpenSeries={onOpenSeries}/>
    {error && <Alert role="alert" className="people-inline-error" data-tone={"destructive"} variant="destructive">
     <AlertCircle size={16} aria-hidden="true" /><AlertDescription>{error}</AlertDescription>
    </Alert>}
    {dirty && <footer className="people-person-save">
     <p className="text-sm text-muted-foreground">保存此人物的对应、替换方式和全部位置记录。</p>
     <div className="flex gap-3">
      <UiButton type="button" onClick={reset} variant="ghost" aria-label={`放弃${actor.label}未保存修改`}>放弃修改</UiButton>
      <UiButton type="button" disabled={!draft.label.trim() || busy} onClick={() => save(onPatch, {source_actors: project.source_actors.map(other => other.id === actor.id ? draft : other)})} aria-label={`保存${actor.label}对应与定位`}><Save aria-hidden="true" data-icon="inline-start" />保存人物</UiButton>
     </div>
    </footer>}
   </fieldset>
 </section>,inspectorHost)}
 </>;
}

export function SceneStage({project,segment,onPatch,onUpload,onImport,onAction,onSelectSegment,
 onOpenSupport,onPrevious,onNext,draftCount=0,busy=false,error,draftTarget}){
 const [inspectorOpen,setInspectorOpen]=useState(true),[filesHost,setFilesHost]=useState(null);
 const inspector=useRef(null),inspectorToggle=useRef(null),appliedTarget=useRef(null);
 const segmentField=useId();
 useEffect(()=>{inspector.current?.scrollTo({top:0});},[segment?.id]);
 useEffect(()=>{
  if(draftTarget?.step!==2||appliedTarget.current===draftTarget)return;
  if(draftTarget.segmentId&&draftTarget.segmentId!==segment?.id)return;
  const targetId=['edit_mask','protection_mask'].includes(draftTarget.maskKind)?'scene-segment-files'
   :draftTarget.targetId==='scene-settings'||draftTarget.sceneDetails?'scene-settings':null;
  if(!targetId)return;
  setInspectorOpen(true);
  const frame=requestAnimationFrame(()=>{
   const target=document.getElementById(targetId);
   if(!target)return;
   appliedTarget.current=draftTarget;
   target?.scrollIntoView({block:'nearest'});
   target?.focus({preventScroll:true});
  });
  return()=>cancelAnimationFrame(frame);
 },[draftTarget,project.id,segment?.id]);
 function closeInspector(){
  if(inspector.current?.contains(document.activeElement))inspectorToggle.current?.focus({preventScroll:true});
  setInspectorOpen(false);
 }
 return <div className="scene-stage" data-testid="scene-stage">
  <div className="scene-stage-toolbar">
   {project.segments.length>1?<Field className="scene-segment-picker">
    <FieldLabel htmlFor={segmentField}>处理片段</FieldLabel>
    <Select value={segment?.id||''} onValueChange={onSelectSegment} disabled={busy}>
     <SelectTrigger id={segmentField} aria-label="选择处理片段"><SelectValue placeholder="选择片段">{segment?`${segment.name||segment.id} · ${time(segment.start)} — ${time(segment.end)}`:undefined}</SelectValue></SelectTrigger>
     <SelectContent position="popper"><SelectGroup>
      {project.segments.map(item=><SelectItem key={item.id} value={item.id}>{item.name||item.id} · {time(item.start)} — {time(item.end)}</SelectItem>)}
     </SelectGroup></SelectContent>
    </Select>
   </Field>:<div className="scene-segment-context">
    <span className="text-sm text-muted-foreground">处理片段</span>
    <span className="text-sm font-medium">{segment?.name||segment?.id||'尚未设置分段'}</span>
    {segment&&<span className="text-sm text-muted-foreground tabular-nums">{time(segment.start)} — {time(segment.end)}</span>}
   </div>}
   <div className="flex items-center gap-3">
    {onOpenSupport&&<UiButton type="button" onClick={event=>onOpenSupport('reference',event)} variant="ghost">原片预览</UiButton>}
    <UiButton ref={inspectorToggle} type="button" aria-label={inspectorOpen?'收起场景设置':'查看场景设置'} title={inspectorOpen?'收起场景设置':'查看场景设置'} aria-expanded={inspectorOpen} aria-controls="scene-inspector" onClick={()=>inspectorOpen?closeInspector():setInspectorOpen(true)} variant="ghost" size="icon">
     {inspectorOpen?<PanelRightClose aria-hidden="true" />:<PanelRightOpen aria-hidden="true" />}
    </UiButton>
   </div>
  </div>
  <div className="scene-work-grid" data-inspector-open={inspectorOpen}>
   <div className="scene-main">
    <MaskPanel draftTarget={draftTarget} project={project} segment={segment} onPatch={onPatch} onUpload={onUpload} onImport={onImport}
     onAction={onAction} onSelectSegment={onSelectSegment} busy={busy} error={error} inspectorHost={filesHost}/>
   </div>
   <aside id="scene-inspector" ref={inspector} className="scene-inspector" hidden={!inspectorOpen} aria-label="场景属性与本段文件">
    <SceneSettings project={project} segment={segment} onPatch={onPatch} onUpload={onUpload} onImport={onImport}
     busy={busy} draftTarget={draftTarget}/>
    <Card className="scene-inspector-panel" id="scene-segment-files" tabIndex={-1} hidden={!segment}>
     <CardHeader><CardTitle><h2 className="text-lg font-semibold">本段材料</h2></CardTitle></CardHeader>
     <CardContent><div ref={setFilesHost} className="scene-inspector-files"/></CardContent>
    </Card>
   </aside>
  </div>
  {(onPrevious||onNext)&&<footer className="scene-step-navigation" aria-label="当前步骤与下一步">
   <Separator className="scene-navigation-divider" />
   {draftCount>0&&<p role="status" className="text-sm text-warning">有修改待记录或保存，切换步骤不会自动保存。</p>}
   <div className="flex gap-3 flex-wrap">
    {onPrevious&&<UiButton type="button" onClick={onPrevious} variant="outline"><ArrowLeft aria-hidden="true" data-icon="inline-start" />上一步</UiButton>}
    {onNext&&<UiButton type="button" onClick={onNext} variant={draftCount?'outline':'default'}>下一步：生成视频<ArrowRight aria-hidden="true" data-icon="inline-end" /></UiButton>}
   </div>
  </footer>}
 </div>;
}

function MaskPanel(props){
 return <SceneMaskPanel {...props} PolygonEditor={PolygonMaskEditor}/>;
}

export function PolygonMaskEditor(props){
 const identity=`${props.project.id}mask-work${props.segment.id}`;
 return <SceneMaskEditor key={identity} {...props} useDraft={useDraft} workingDraft={MASK_WORK.get(identity)} onWorkingDraft={rememberMaskWorking}/>;
}

function latestPlan(project,jobs){return project.replacement_plan||project.preparation?.plan||null;}
function WanCompatibility({plan}){
 const issues=plan?.wan_compatibility?.issues||[];
 if(!issues.length)return null;
 return <section className="wan-compatibility"><h3  className="text-lg font-semibold leading-snug tracking-tight">Wan 云端能力核对</h3><p>以下限制影响后续上传和生成。本地定位、素材整理、遮罩制作与原声预演可以继续。</p><ul className="issue-list">{issues.map((item,index)=><li key={item.code||index} className="blocking">{typeof item==='string'?item:item.message}</li>)}</ul></section>;
}
export function groupReplacementIssues(plan){
 const groups=[{id:'identity',title:'人物与全剧身份',step:1,issues:[]},{id:'shots',title:'镜头与遮挡区间',step:1,issues:[]},{id:'range',title:'场景与像素保护范围',step:2,issues:[]},{id:'source',title:'原片、素材与模型参数',step:0,issues:[]}];
 for(const issue of plan?.issues||[]){const code=issue.code||'';const group=/^SHOT_|OCCLUSION|ACTOR_OVERLAP/.test(code)?groups[1]:/MASK|POLYGON|SCENE_|REGION_|LOCATORS_ONLY/.test(code)?groups[2]:/ACTOR|IDENTITY|TARGET_|SERIES_|PERSON_REFERENCE/.test(code)?groups[0]:groups[3];group.issues.push(issue);}
 for(const issue of plan?.wan_compatibility?.issues||[])groups[3].issues.push({...issue,code:issue.code||'WAN_COMPATIBILITY'});
 return groups.filter(group=>group.issues.length);
}

function IssueGroups({plan,onStep}){
 const groups=groupReplacementIssues(plan);
 const [groupDisclosures,setGroupDisclosures]=useState(()=>Object.fromEntries(groups.map(group=>{
  const basis=group.issues.some(issue=>issue.blocking);return [group.id,{basis,open:basis}];
 })));
 const defaultsChanged=Object.keys(groupDisclosures).length!==groups.length||groups.some(group=>
  groupDisclosures[group.id]?.basis!==group.issues.some(issue=>issue.blocking));
 if(defaultsChanged)setGroupDisclosures(value=>Object.fromEntries(groups.map(group=>{
  const basis=group.issues.some(issue=>issue.blocking),previous=value[group.id];
  return [group.id,previous?.basis===basis?previous:{basis,open:basis}];
 })));
 return <div className="review-issue-groups">{groups.map(group=>{const blockers=group.issues.filter(i=>i.blocking).length;return <UiCollapsible className="review-issue-group group/disclosure" key={group.id} data-testid={`issue-group-${group.id}`} open={groupDisclosures[group.id]?.open ?? blockers > 0} onOpenChange={open => setGroupDisclosures(value => ({...value,[group.id]:{basis:blockers > 0,open}}))}><UiCollapsibleTrigger asChild><UiButton type="button" variant="outline" className="h-auto w-full justify-between whitespace-normal py-2 text-left"><span>{group.title}</span><strong>{blockers} 项待处理 · {group.issues.length-blockers} 项提示</strong><ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4"><ul className="issue-list">{group.issues.map((issue,i)=><li key={`${issue.code}-${i}`} className={issue.blocking?'blocking':'warning'}><strong>{issue.blocking?'待处理':'提示'}</strong> {issue.message}<small>{issue.segment_id?`片段 ${issue.segment_id}`:''}</small></li>)}</ul>{onStep&&<UiButton onClick={()=>onStep(group.step,group.step===1?'identity-mapping':group.step===2?'mask-panel':'source-import')} variant={'outline'} type="button">{group.step===1?'前往人物对应核对':group.step===2?'前往替换范围处理':'查看原片与参数依据'}</UiButton>}</UiCollapsibleContent></UiCollapsible>;})}</div>;
}

function SubmissionInputs({project,segment,submissionInputs}){
 return <section className="review-inputs" data-testid="submission-input-summary"><h3  className="text-lg font-semibold leading-snug tracking-tight">全部片段的实际视频文件</h3><p className="review-next-step">每行对应一个独立短片。原片全片只用于浏览；当前片段的参考图与提示词在上方单独核对。</p><div className="table-scroll"><Table className="mapping-table"><TableHeader><TableRow><TableHead>任务片段</TableHead><TableHead>原片范围</TableHead><TableHead>文件时长</TableHead><TableHead>本地视频与状态</TableHead></TableRow></TableHeader><TableBody>{project.segments.map(s=>{const verified=project.preparation&&submissionInputs?.find(input=>input.segment_id===s.id&&input.asset_id===s.reference?.id);const duration=verified?.duration_seconds??s.reference?.meta?.video_duration_seconds??s.reference?.meta?.duration_seconds;return <TableRow key={s.id} aria-current={segment?.id===s.id?'true':undefined}><TableHead scope="row">{s.name||s.id}{segment?.id===s.id?' · 当前选中':''}</TableHead><TableCell>{time(s.start)} — {time(s.end)}</TableCell><TableCell>{s.reference&&Number.isFinite(duration)?`${Number(duration).toFixed(3)} 秒`:'尚未制作'}</TableCell><TableCell>{s.reference?<><a href={assetUrl(s.reference)} target="_blank" rel="noreferrer">查看{s.id}段视频</a><small>{verified?.name||s.reference.name}</small><small className={!project.preparation?'pending-text':''}>{!project.preparation?'旧文件 · 当前准备记录需要更新':verified?'当前文件已通过本次预检核对':'文件已制作 · 尚未完成当前预检'}</small></>:<span>请先制作全部片段文件</span>}</TableCell></TableRow>;})}</TableBody></Table></div></section>;
}

export function ReplacementCheckStage(props){
 return <GenerationStage {...props} useDraft={useDraft} checksDefinition={CHECKS} PreparationLinks={PreparationLinks}/>;
}

export function PreparationLinks({project,generationUi=false}){
 const prep=project.preparation||{};
 const candidates=[['定位联系表',prep.contact_sheet||prep.contact_sheet_asset],['准备清单',prep.manifest_asset],['定位预览',prep.preview_asset]];
 if(generationUi){
  const available=candidates.filter(([,asset])=>asset);
  if(!available.length)return null;
  return <GenerationDisclosure className="generation-preparation-links" label="准备文件">
   <div className="flex gap-3 flex-wrap">
    {available.map(([label,asset])=><UiButton key={label} asChild variant="outline" size="default">
     <a href={assetUrl(asset)} target="_blank" rel="noreferrer">查看{label}</a>
    </UiButton>)}
   </div>
   {prep.contact_sheet?.url&&
    <div className="flex flex-col items-start gap-3">
     <p className="text-sm text-muted-foreground mb-0">此图用于检查已标记的身份，不代表逐帧遮罩或动作审核已通过。</p>
     <a href={prep.contact_sheet.url} target="_blank" rel="noreferrer"><img className="contact-sheet-preview" src={prep.contact_sheet.url} alt="原片人物定位联系表" loading="lazy"/></a>
    </div>}
  </GenerationDisclosure>;
 }
 return <div className="preparation-links">{candidates.filter(([,a])=>a).map(([label,a])=><UiButton asChild variant="outline"><a key={label} href={assetUrl(a)} target="_blank" rel="noreferrer">查看{label}</a></UiButton>)}{prep.contact_sheet?.url&&<UiCollapsible className="advanced-panel group/disclosure"><UiCollapsibleTrigger asChild><UiButton type="button" variant="outline" className="h-auto w-full justify-between whitespace-normal py-2 text-left">展开人物定位核对图（抽样检查）<ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4"><p>此图用于检查已标记的身份，不代表逐帧遮罩或动作审核已通过。</p><a href={prep.contact_sheet.url} target="_blank" rel="noreferrer"><img className="contact-sheet-preview" src={prep.contact_sheet.url} alt="原片人物定位联系表" loading="lazy"/></a></UiCollapsibleContent></UiCollapsible>}</div>;
}

export function ReplacementExportStage(props){
 return <ResultStage {...props} checksDefinition={CHECKS}/>;
}

export function ReplacementInspector({project,segment,onPatch,onAction,onStep,jobs,config,onConfig,busy,step=0}){const _themeFieldId=useId();
 const plan=latestPlan(project,jobs),settings=project.settings||{},source=project.source_meta||{};
 const pre=jobs.find(j=>j.action==='preflight'&&j.result?.plan?.fingerprint===plan?.fingerprint),estimate=pre?.result;
 const confirmed=(project.source_actors||[]).reduce((n,a)=>n+(a.observations||[]).filter(o=>o.confirmed).length,0),observations=(project.source_actors||[]).reduce((n,a)=>n+(a.observations||[]).length,0);
 return <aside className="inspector replacement-inspector contextual-inspector" data-testid="replacement-inspector">
  {step===0&&<section><h2  className="text-xl font-semibold leading-snug tracking-tight">这一步完成后</h2><p>原视频与分段范围保存在本机。下一步指定需要替换的人物及新角色。</p><dl className="preservation-list"><div><dt>原片时长</dt><dd>{time(source.duration)}</dd></div><div><dt>已保存分段</dt><dd>{project.segments.length} 段</dd></div><div><dt>原片帧率</dt><dd>{Number(source.fps||0).toFixed(3)} 帧/秒</dd></div><div><dt>声音</dt><dd>{source.has_audio?'沿用连续原声':'原片无音轨'}</dd></div></dl><p>动作、镜头、节奏和原声作为后续验收依据，不通过改变速度来对齐。</p></section>}
  {step===1&&<section><h2  className="text-xl font-semibold leading-snug tracking-tight">人物对应检查</h2><p>原片中的同一个人，跨镜头沿用同一份人物档案。新角色参考图只提供外观。</p><dl className="preservation-list"><div><dt>原片人物</dt><dd>{project.source_actors?.length||0} 人</dd></div><div><dt>新角色</dt><dd>{project.roles.length} 个</dd></div><div><dt>已确认位置</dt><dd>{confirmed} / {observations} 处</dd></div></dl><p>先认人，再选新造型，最后逐处核对出现位置。原片左右位置变化不能用来重新分配角色。</p><UiButton className="w-full" onClick={()=>onStep(2,'mask-panel')} variant={'outline'} type="button">继续设置替换范围</UiButton></section>}
  {step===2&&<section><h2  className="text-xl font-semibold leading-snug tracking-tight">当前片段的范围</h2><p>{segment?.name||'请先保存分段'}</p><dl className="preservation-list"><div><dt>替换遮罩</dt><dd>{segment?.edit_mask?'已导入文件':'尚未制作或导入'}</dd></div><div><dt>前景保护</dt><dd>{segment?.protection_mask?'已导入文件':'可选'}</dd></div><div><dt>轮廓关键帧</dt><dd>{segment?.mask_keyframes?.length||0} 帧</dd></div></dl><p>替换遮罩记录原片上需要变更的区域。保护遮罩记录应保留的前景。</p><p>手、武器、球拍或其它物体从人物前方经过时，请仔细检查保护范围。</p></section>}
  {step===3&&<><section id="wan-settings" tabIndex="-1"><h2  className="text-xl font-semibold leading-snug tracking-tight">Wan 生成设置</h2><Field><FieldLabel htmlFor={_themeFieldId + "-0"}>{"视频模型"}</FieldLabel><NativeSelect aria-label="视频模型" value={settings.model||'wan3.0-video'} onChange={e=>onPatch({settings:{...settings,model:e.target.value,watermark:false,audio:false}})} id={_themeFieldId + "-0"}><option value="wan3.0-video">阿里万相 Wan 3.0</option><option value="wan3.0-video-prime">阿里万相 Wan 3.0 Prime</option></NativeSelect></Field><UiButton onClick={()=>onStep(0,'delivery-spec')} variant={"link"} size={"default"} type="button">交付档位 {settings.resolution||'待选择'} · 前往第 1 步</UiButton><p>更改后立即保存；需要重新准备材料和审核。</p><dl className="preservation-list"><div><dt>画面比例</dt><dd>随原片（固定）</dd></div><div><dt>模型声音</dt><dd>关闭，导出时接回原声</dd></div><div><dt>水印</dt><dd>关闭</dd></div></dl><UiCollapsible className="advanced-panel group/disclosure"><UiCollapsibleTrigger asChild><UiButton type="button" variant="outline" className="h-auto w-full justify-between whitespace-normal py-2 text-left">模型与原片参数说明<ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4"><p>原片帧率：{source.fps_rational||source.fps||'随原片'}。生成结果参数需要单独检查，不能以自动调速或补帧替代。</p><Field><FieldLabel htmlFor={_themeFieldId + "-1"}>{"生成画面比例"}</FieldLabel><NativeSelect value="adaptive" aria-label="候选画面比例" disabled id={_themeFieldId + "-1"}><option value="adaptive">随原片（固定）</option></NativeSelect></Field><p>当前接入 Wan 3.0；后续 Wan 版本经能力核对后适配。</p></UiCollapsibleContent></UiCollapsible></section><section><h2  className="text-xl font-semibold leading-snug tracking-tight">费用估算与提交状态</h2>{!estimate?<p>尚无当前材料版本的费用估算。保存并制作材料后，可在本机估算。</p>:estimate.estimate_complete===false?<Alert tone="warning" role="note"><AlertDescription>费用尚未完整估算。已核对部分预留 ¥{Number(estimate.total_reserved_yuan||0).toFixed(2)}，不能作为全部费用；请处理缺项后重查。</AlertDescription></Alert>:<p>当前估算预留：<strong>¥{Number(estimate.total_reserved_yuan||0).toFixed(2)}</strong><small>仅估算，尚未上传或扣费。</small></p>}{estimate?.blockers?.length>0&&<UiCollapsible className="advanced-panel group/disclosure"><UiCollapsibleTrigger asChild><UiButton type="button" variant="outline" className="h-auto w-full justify-between whitespace-normal py-2 text-left">{estimate.blockers.length} 项提交限制与待核对事项<ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4"><ul className="preflight-blockers">{estimate.blockers.map((item,i)=><li key={i}>{typeof item==='string'?item:item.message||item.code}</li>)}</ul></UiCollapsibleContent></UiCollapsible>}<UiButton className="w-full" onClick={()=>onAction('preflight',{segment_id:null})} variant={'outline'} disabled={(busy==='preflight') || (!project.source)} aria-busy={(busy==='preflight') || undefined} type="button">{(busy==='preflight') && <Spinner data-icon="inline-start" aria-hidden="true" />}估算全部片段费用</UiButton><p>云端上传与付费生成等待确认。本地材料审核不会开启生成权限。</p><UiButton className="w-full" variant={'outline'} disabled type="button">上传并生成（等待确认）</UiButton><UiButton onClick={onConfig} variant={"link"} size={"default"} type="button">{config?.configured?'API 已配置 · 查看设置':'配置百炼 API Key'}</UiButton></section></>}
  {step===4&&<section><h2  className="text-xl font-semibold leading-snug tracking-tight">导出前需要完成</h2><p>逐段检查原始候选并完成六项验收。导出时按原定顺序拼接原始视频流，并接回一次连续原声。</p></section>}
  <UiCollapsible className="inspector-detail group/disclosure"><UiCollapsibleTrigger asChild><UiButton type="button" variant="ghost" className="h-auto w-full justify-between whitespace-normal py-2 text-left">查看原片保留要求<ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4"><dl className="preservation-list"><div><dt>人物动作</dt><dd>沿用原片</dd></div><div><dt>镜头与构图</dt><dd>沿用原片</dd></div><div><dt>节奏与时长</dt><dd>沿用原片</dd></div><div><dt>最终帧率</dt><dd>{source.fps_rational||source.fps||'随原片'}</dd></div><div><dt>声音</dt><dd>原片连续音轨</dd></div><div><dt>水印</dt><dd>关闭</dd></div></dl><p>以上是验收要求；生成结果仍须逐段对照检查。</p></UiCollapsibleContent></UiCollapsible>
 </aside>;
}
