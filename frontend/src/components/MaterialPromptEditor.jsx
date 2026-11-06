import {Collapsible as UiCollapsible,CollapsibleTrigger as UiCollapsibleTrigger,CollapsibleContent as UiCollapsibleContent} from "./ui/collapsible";
import {cn} from "@/lib/utils";
import React,{useEffect,useState} from 'react';
import { Badge } from './ui/badge';

import { Textarea } from './ui/textarea';
import {Button as UiButton} from "./ui/button";
import { AlertDialog, AlertDialogTrigger, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogCancel } from './ui/alert-dialog';
import { Spinner } from './ui/spinner';
import { Alert, AlertTitle, AlertDescription } from './ui/alert';

import {AlertCircle,Save,ChevronDown} from "lucide-react";
import {time} from '../api';

import {GenerationDisclosure} from './GenerationControls';

export default function MaterialPromptEditor({project,segment,prompt,setPrompt,dirty,save,reset,onPatch,onAction,planFingerprint,busy=false,error,generationUi=false,defaultOpen=false}){
 const [rebuildOpen,setRebuildOpen]=useState(false),[rebuilding,setRebuilding]=useState(false);
 useEffect(()=>{setRebuildOpen(false);},[project.id,planFingerprint,segment?.id]);
 const pending=busy||rebuilding;
 const saveDisabled=!segment||!dirty||!prompt.trim();
 const rebuildDisabled=!segment||dirty;
 const state=dirty?'未保存':prompt.trim()?'已保存':'尚无提示词';
 async function rebuild(){
  if(rebuildDisabled||pending)return;
  setRebuilding(true);
  try{
   const result=await onAction('save_prompt');
   if(result)setRebuildOpen(false);
  }finally{setRebuilding(false);}
 }
 function savePrompt(){
  if(saveDisabled||pending)return;
  return save(onPatch,{prompts:{...project.prompts,[segment.id]:prompt}});
 }
 if(generationUi){
  const filled=!!prompt.trim();
  return (
   <GenerationDisclosure defaultOpen={defaultOpen} id="prompt-editor" className="generation-prompt-editor"
    triggerClassName="generation-prompt-trigger" contentClassName="generation-prompt-content"
    label={<>
     <span>完整提示词</span>
     {(dirty||!filled)&&<Badge variant="warning">{state}</Badge>}
    </>}>
    <fieldset className="generation-prompt-fieldset" disabled={pending}>
     <Textarea className="generation-prompt-textarea" aria-label="当前片段完整替换提示词" value={prompt} onChange={event=>setPrompt(event.target.value)} disabled={pending} placeholder="保存人物对应与场景后，可按材料重建提示词。" />
     <p role={dirty?'status':undefined} className={cn("text-sm mb-0", dirty?'text-warning':'text-muted-foreground')}>
      {dirty?'先保存或放弃修改，再按材料重建。':'重建会按已保存材料覆盖并保存本段提示词，之后需重新准备和审核。'}
     </p>
     <div className="flex items-center flex-wrap gap-3">
      {dirty&&<UiButton type="button" disabled={saveDisabled||pending} onClick={savePrompt}>
       <Save aria-hidden="true" data-icon="inline-start" />保存提示词
      </UiButton>}
      {dirty&&<UiButton type="button" onClick={reset} variant={"outline"}>放弃修改</UiButton>}
      {filled?<AlertDialog open={rebuildOpen} onOpenChange={open=>{if(!pending)setRebuildOpen(open);}}>
       <AlertDialogTrigger asChild>
        <UiButton type="button" disabled={rebuildDisabled||pending} variant={"outline"}>
         按材料重建
        </UiButton>
       </AlertDialogTrigger>
       <AlertDialogContent className="generation-prompt-confirm sm:max-w-[520px]" onEscapeKeyDown={event=>{if(pending)event.preventDefault();}} >
        <AlertDialogTitle>覆盖当前片段提示词？</AlertDialogTitle>
        <AlertDialogDescription>
         将按已保存材料重建并立即保存，覆盖当前提示词。相关准备和审核需要更新；确认后加入本地处理队列。
        </AlertDialogDescription>
        <div className="flex justify-end items-center gap-3">
         <AlertDialogCancel asChild>
          <UiButton type="button" disabled={pending} variant={"outline"}>取消</UiButton>
         </AlertDialogCancel>
         <UiButton type="button" onClick={rebuild} disabled={Boolean(rebuilding) || (rebuildDisabled||pending)} aria-busy={(rebuilding) || undefined}>{(rebuilding) && <Spinner data-icon="inline-start" aria-hidden="true" />}
          覆盖并保存
         </UiButton>
        </div>
       </AlertDialogContent>
      </AlertDialog>:<UiButton type="button" onClick={rebuild} variant={"outline"} disabled={Boolean(rebuilding) || (rebuildDisabled||pending)} aria-busy={(rebuilding) || undefined}>{(rebuilding) && <Spinner data-icon="inline-start" aria-hidden="true" />}按材料重建</UiButton>}
     </div>
    </fieldset>
    {error&&<Alert role="alert" data-tone={"destructive"} variant="destructive">
     <AlertCircle size={16} aria-hidden="true"/>
     <AlertDescription>{error}</AlertDescription>
    </Alert>}
   </GenerationDisclosure>
  );
 }
 return <UiCollapsible className="material-prompt-editor group/disclosure" id="prompt-editor" tabIndex="-1">
  <UiCollapsibleTrigger asChild><UiButton type="button" variant="ghost" className="h-auto w-full justify-between whitespace-normal py-2 text-left material-prompt-summary"><span>查看与编辑完整提示词</span><Badge variant={dirty || !prompt.trim() ? 'warning' : 'secondary'}>{state}</Badge><span className="material-prompt-count">{Array.from(prompt).length} 字符</span><ChevronDown aria-hidden="true" data-icon="inline-end" className="ml-auto shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180" /></UiButton></UiCollapsibleTrigger><UiCollapsibleContent forceMount className="pt-4">
  <div className="material-prompt-body">
   <p className="material-prompt-scope">当前片段 · {segment?.name||'尚未选择片段'}{segment?` · 原片 ${time(segment.start)} — ${time(segment.end)}`:''}</p>
   <fieldset className="material-prompt-fieldset" disabled={pending}>
    <Textarea className="prompt-editor material-prompt-textarea" aria-label="当前片段完整替换提示词" value={prompt} onChange={event=>setPrompt(event.target.value)} placeholder="保存人物对应与场景后，可按材料重建提示词。" />
    <p className="material-prompt-note" role={dirty?'status':undefined}>{dirty?'当前片段有未保存修改。先保存或放弃修改，才能按材料重建。':'保存只更新当前片段提示词；修改后，相关分段准备和人工审核需要更新。'}</p>
    <div className="button-row material-prompt-actions"><UiButton onClick={savePrompt} variant={'default'} disabled={saveDisabled} type="button"><Save data-icon="inline-start" />保存当前片段提示词</UiButton>{dirty&&<UiButton onClick={reset} variant={'outline'} type="button">放弃未保存修改</UiButton>}<UiButton onClick={()=>prompt.trim()?setRebuildOpen(true):rebuild()} variant={'outline'} disabled={rebuildDisabled} type="button">按当前材料重建并保存提示词</UiButton></div>
    {!dirty&&<p className="material-prompt-note">重建会按已保存材料生成模板并立即保存，覆盖本段原有提示词；相关准备和审核需要更新。</p>}
    {rebuildOpen&&<Alert tone="warning" role="alert" className="material-prompt-confirm"><AlertTitle>将覆盖当前片段已保存的提示词</AlertTitle><AlertDescription><p>新的模板会立即保存，相关准备和审核需要更新。确认后将加入本地处理队列。</p><div className="button-row"><UiButton onClick={rebuild} variant={'default'} disabled={rebuildDisabled} type="button">确认覆盖并保存</UiButton><UiButton onClick={()=>setRebuildOpen(false)} variant={'outline'} type="button">取消覆盖</UiButton></div></AlertDescription></Alert>}
   </fieldset>
   {error&&<Alert tone="destructive" role="alert"><AlertDescription>{error}</AlertDescription></Alert>}
  </div>
 </UiCollapsibleContent></UiCollapsible>;
}
