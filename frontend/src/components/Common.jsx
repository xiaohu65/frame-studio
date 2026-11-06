import React,{useId,useRef,useState} from 'react';
import {Upload,FolderInput,X,Loader2,Check,AlertCircle,ExternalLink,Clock3,ChevronDown} from 'lucide-react';
import {assetUrl,statusLabel} from '../api';
import { Button as UiButton } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Empty as EmptyState, EmptyHeader, EmptyMedia, EmptyDescription, EmptyTitle } from '@/components/ui/empty';
import { Field as UiField, FieldLabel, FieldDescription, FieldError } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';

export function Button({children,busy=false,variant='outline',className='',...props}) {
 return <UiButton className={cn(className)} {...props} variant={variant} aria-busy={busy||undefined} disabled={busy||props.disabled}>{busy&&<Spinner />}{children}</UiButton>;
}
export function Status({value}) {
 const variant=['READY','SUCCEEDED','COMPLETED'].includes(value)?'success':value==='FAILED'?'destructive':['RUNNING','PROCESSING','QUEUED'].includes(value)?'info':['NEEDS_REVIEW','UNKNOWN'].includes(value)?'warning':'secondary';
 return <Badge variant={variant} data-status={value||'DRAFT'} className="self-center">{statusLabel(value)}</Badge>;
}
export function Empty({children,className,variant='default'}){return <EmptyState className={className} variant={variant}><EmptyHeader><EmptyMedia variant="icon"><FolderInput aria-hidden="true"/></EmptyMedia><EmptyDescription>{children}</EmptyDescription></EmptyHeader></EmptyState>;}
export function Notice({children,tone='info'}){
 const semanticTone=({error:'destructive',destructive:'destructive',warning:'warning',info:'info',success:'success'})[tone]||'info';
 return <Alert className="my-3" variant={semanticTone==='destructive'?'destructive':'default'} data-tone={semanticTone} role={semanticTone==='destructive'?'alert':undefined}><AlertCircle aria-hidden="true"/><AlertDescription>{children}</AlertDescription></Alert>;
}
export function Field({label,hint,children,className=''}){
 const fieldId=useId();
 const controls=React.Children.map(children,child=>React.isValidElement(child)?React.cloneElement(child,{id:child.props.id||fieldId,'aria-describedby':hint?[child.props['aria-describedby'],fieldId+'-hint'].filter(Boolean).join(' '):child.props['aria-describedby']}):child);
 const target=React.Children.toArray(children).find(React.isValidElement)?.props.id||fieldId;
 return <UiField className={`field ${className}`}><FieldLabel htmlFor={target}>{label}</FieldLabel>{controls}{hint&&<FieldDescription id={fieldId+'-hint'}>{hint}</FieldDescription>}</UiField>;
}
export function UploadButton({onFiles,accept,multiple=false,children='上传文件',disabled=false,variant='outline'}){
 const ref=useRef();const [busy,setBusy]=useState(false);
 async function changed(e){const files=[...e.target.files];if(!files.length)return;setBusy(true);try{await onFiles(files);}finally{setBusy(false);e.target.value='';}}
 return <><input ref={ref} type="file" className="visually-hidden" disabled={disabled||busy} accept={accept} multiple={multiple} onChange={changed} aria-label={typeof children==='string'?children:'选择本机文件'}/><Button onClick={()=>ref.current.click()} busy={busy} disabled={disabled} variant={variant}><Upload size={16}/>{children}</Button></>;
}
export function ImportPath({onImport,label='或填写本机文件路径',kindLabel='文件',disabled=false}){
 const [open,setOpen]=useState(false),[path,setPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 async function submit(e){
  e.preventDefault();if(!path.trim())return;setBusy(true);setError('');
  try{
   const result=await onImport(path.trim());
   // App.safe returns null after a rejected import. Keep the path for a retry.
   if(result===null){setError('文件未能导入。请查看页面中的错误说明，修正路径后重试。');return;}
   setPath('');setOpen(false);
  }catch(e){setError(e.message||'文件未能导入，请检查路径后重试。');}
  finally{setBusy(false);}
 }
 return <div className="path-import">{!open?<UiButton type="button" variant="link" className="h-auto justify-start px-0" disabled={disabled} onClick={()=>setOpen(true)}><FolderInput size={14}/>{label}</UiButton>:<><form onSubmit={submit}><Input aria-label={`${kindLabel}绝对路径`} placeholder="例如：/Users/你的名字/Movies/视频.mp4" value={path} onChange={e=>{setPath(e.target.value);setError('');}} required disabled={busy||disabled}/><Button type="submit" busy={busy} disabled={disabled}>导入此文件</Button><UiButton type="button" variant="ghost" size="icon-sm" aria-label="取消导入" disabled={busy} onClick={()=>setOpen(false)}><X aria-hidden="true"/></UiButton></form><small className="path-import-help">使用此路径引用本机文件，原文件不会复制到项目中。导入后请保留原文件的位置。</small>{error&&<FieldError>{error}</FieldError>}</>}</div>;
}
export function AssetTile({asset,onRemove,label}){return <div className="asset-tile"><a href={assetUrl(asset)} target="_blank" rel="noreferrer"><img src={asset.thumbnail_url||assetUrl(asset)} alt={label||asset.name} loading="lazy"/></a><div><strong>{label||asset.name}</strong><small title={asset.name}>{asset.name}</small></div>{onRemove&&<UiButton type="button" variant="ghost" size="icon-sm" className="remove-asset" onClick={onRemove} aria-label={`移除${asset.name}`}><X aria-hidden="true"/></UiButton>}</div>;}

export function jobFeedback(job,project){
 const result=job.result||{};
 if(job.status==='QUEUED')return {label:'等待本地处理',tone:'progress',detail:'任务已加入队列，处理结果会自动显示在这里。'};
 if(job.status==='RUNNING')return {label:'正在处理',tone:'progress',detail:job.message||'正在本机处理，请保留工作台运行。'};
 if(job.status==='FAILED')return {label:'处理失败',tone:'error',detail:job.message||'本次未完成。请检查材料和页面中的错误说明后重试。'};
 if(job.status==='UNKNOWN')return {label:'需要检查结果',tone:'warning',detail:job.message||'上次处理状态无法确认，请先查看现有结果，再决定是否重试。'};
 if(job.status!=='SUCCEEDED')return {label:statusLabel(job.status),tone:'neutral',detail:job.message};
 const currentFingerprint=project?.replacement_plan?.fingerprint||project?.preparation?.fingerprint;
 const planFingerprint=result.plan?.fingerprint||(['package','review_replacement'].includes(job.action)?result.fingerprint||result.review?.fingerprint:null);
 const outdated=project?.workflow==='replacement'&&planFingerprint&&(!currentFingerprint||planFingerprint!==currentFingerprint);
 let state={label:'任务已完成',tone:'neutral',detail:job.message||'结果已保存在本机。'};
 if(job.action==='replacement_check'&&result.plan){
  const pending=result.plan?.issues?.filter(i=>i.blocking).length||0;
  const wanPending=result.plan?.wan_compatibility?.ready===false;
  state={label:pending||wanPending?'检查结束 · 仍待处理':'材料检查通过 · 待人工审核',tone:pending||wanPending?'warning':'success',detail:pending?`仍有 ${pending} 项材料问题，请到“准备并检查 Wan 材料”逐项处理。`:wanPending?'材料检查已结束，Wan 参数兼容性仍有待处理项。':'材料条件检查已通过；仍需人工审核，上传与生成另行确认。'};
 }else if(job.action==='preflight'&&typeof result.estimate_complete==='boolean'){
  const amount=Number.isFinite(result.total_reserved_yuan)?`¥${result.total_reserved_yuan.toFixed(2)}`:null;
  const incomplete=result.estimate_complete===false;
  state={label:incomplete?'费用尚未完整估算':'费用检查完成 · 待确认',tone:incomplete?'warning':'neutral',detail:incomplete?`已核对 ${result.reports?.length||0} 段${amount?`，部分费用预留 ${amount}`:''}。这不是全部费用，请处理缺项后重新估算。`:`本地费用预留${amount?` ${amount}`:''}；未访问云端，尚未上传或扣费。`};
 }else if(job.action==='adapt_candidate'){
  state={label:'本地适配通过 · 待实际验收',tone:'neutral',detail:job.message};
 }else if(job.action==='validate_candidate'){
  const ready=result.report?.ready;
  state={label:ready?'格式与时间通过 · 待外观复核':'检查结束 · 格式或时间待处理',tone:ready?'neutral':'warning',detail:ready?'人物身份、动作和遮挡仍需对照原片复核。':job.message||'请展开记录查看尺寸、帧率、帧数或时长问题。'};
 }else if(job.action==='prepare_replacement'){
  state={label:'文件已制作 · 待检查',tone:'neutral',detail:'分段参考与人物核对图已保存到本机。制作完成不代表材料审核通过。'};
 }else if(job.action==='review_replacement'||job.action==='review_candidate'){
  state={label:'本地复核已记录',tone:'success',detail:job.message||'复核记录已保存，上传与生成授权单独确认。'};
 }else if(job.action==='composite_replacement'){
  state={label:'合成已完成 · 待结果复核',tone:'neutral',detail:job.message};
 }else if(job.action==='assemble_preview'){
  state={label:'原片预演已导出',tone:'neutral',detail:'用于检查原片拼接与原声；此文件尚未替换人物。'};
 }else if(['assemble','package','extract_audio','render_masks'].includes(job.action)){
  state={label:job.action==='package'?'素材包已导出 · 审核状态见当前材料':'文件已保存',tone:'neutral',detail:job.message};
 }
 return {...state,outdated};
}

function JobRecord({job,project}){
 const feedback=jobFeedback(job,project);
 const appearance=({error:'destructive',warning:'warning',success:'success',progress:'info'})[feedback.tone]||'secondary';
 return <Collapsible className="job-row"><CollapsibleTrigger asChild><UiButton type="button" variant="ghost" className="h-auto w-full justify-start whitespace-normal py-3 text-left"><span aria-hidden="true">{job.status==='RUNNING'?<Spinner />:job.status==='QUEUED'?<Clock3 />:feedback.tone==='success'?<Check />:feedback.tone==='warning'||feedback.tone==='error'?<AlertCircle />:<Check />}</span><span className="job-info"><strong>{actionName(job.action)}{job.segment_id?` · ${job.segment_id}段`:''}</strong><small>{feedback.detail}</small>{feedback.outdated&&<small className="job-outdated">旧版本记录，仅供查看；请以当前材料检查为准。</small>}</span><Badge variant={appearance}>{feedback.label}</Badge><ChevronDown data-icon="inline-end" aria-hidden="true" /></UiButton></CollapsibleTrigger><CollapsibleContent className="job-details"><p>{job.message}</p>{job.result&&<ResultView result={job.result}/>}</CollapsibleContent></Collapsible>;
}
export function JobList({jobs=[],compact=false,project}){
 if(!jobs.length)return <section className="job-list" aria-label="处理进度与记录"><EmptyState><EmptyHeader><EmptyTitle>处理进度与记录</EmptyTitle><EmptyDescription>保存材料并执行本地操作后，这里会显示处理进度、完成结果和失败原因。</EmptyDescription></EmptyHeader></EmptyState></section>;
 const visible=jobs.slice(0,compact?3:8),running=visible.filter(j=>['RUNNING','QUEUED'].includes(j.status)),finished=visible.filter(j=>!['RUNNING','QUEUED'].includes(j.status));
 const latest=running.length?null:finished[0],history=latest?finished.slice(1):finished;
 return <section className={`job-list ${compact?'compact':''}`} aria-label="处理进度与记录"><div className="job-section-heading"><h3>处理进度与记录</h3><span>本机任务 · 不代表已提交 Wan</span></div><div className="job-current" aria-live="polite">{running.map(j=><JobRecord key={j.id} job={j} project={project}/>)}{latest&&<JobRecord job={latest} project={project}/>}</div>{history.length>0&&<Collapsible className="job-history"><CollapsibleTrigger asChild><UiButton type="button" variant="ghost">查看较早的处理记录（{history.length} 条）<ChevronDown data-icon="inline-end" aria-hidden="true" /></UiButton></CollapsibleTrigger><CollapsibleContent>{history.map(j=><JobRecord key={j.id} job={j} project={project}/>)}</CollapsibleContent></Collapsible>}</section>;
}
export function actionName(a){return {prepare_generation:'准备生成材料与费用',render_masks:'制作本地黑白遮罩',prepare_replacement:'制作分段文件与人物核对图',replacement_check:'检查人物、场景与范围',review_replacement:'记录生成前材料复核',adapt_candidate:'本地适配30fps候选',validate_candidate:'检查生成视频的格式与时间',composite_replacement:'按替换范围合成本段',review_candidate:'记录生成结果复核',assemble_preview:'导出原片拼接与原声预演',analyze_pose:'分析关节与遮挡',render_puppet:'制作关节人偶',save_pose:'保存关节数据',extract_audio:'提取独立原声音轨',separate_music:'分离背景音乐',preflight:'检查 Wan 材料与费用',submit:'提交万相生成',poll:'查询生成状态',download:'保存生成视频',assemble:'拼接成片并接入原声',package:'导出本地素材包',save_prompt:'保存提示词'}[a]||a;}
export function ResultView({result}) {
 const text=typeof result==='string'?result:JSON.stringify(result,null,2);const link=result?.url||result?.asset?.url;
 return <>{link&&<UiButton asChild variant="link" className="h-auto justify-start px-0"><a href={link} download><ExternalLink data-icon="inline-start" aria-hidden="true" />查看或下载处理结果</a></UiButton>}<Collapsible className="result-technical"><CollapsibleTrigger asChild><UiButton type="button" variant="ghost">查看处理详情<ChevronDown data-icon="inline-end" aria-hidden="true" /></UiButton></CollapsibleTrigger><CollapsibleContent><pre>{text}</pre></CollapsibleContent></Collapsible></>;
}
