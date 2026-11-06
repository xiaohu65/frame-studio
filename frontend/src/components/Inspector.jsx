import React,{useEffect,useId,useState} from 'react';
import { Button as UiButton } from "@/components/ui/button";
import { Field as FormField, FieldGroup, FieldLabel, FieldDescription } from "@/components/ui/field";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Spinner } from "@/components/ui/spinner";
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogClose } from "@/components/ui/dialog";
import {ChevronRight,Search,Play,ArrowRight,ShieldCheck,Save,ExternalLink,AlertCircle,X} from 'lucide-react';
import {Button,Field,Notice} from './Common';

export function Inspector({project,segment,onPatch,onAction,onStep,jobs,busy,config,onConfig}){
 const auth=project.authorization||{};const [budget,setBudget]=useState(auth.budget_yuan||'');
 useEffect(()=>setBudget(auth.budget_yuan||''),[project.id,auth.budget_yuan]);
 const params=project.settings||{};
 const pre=jobs.find(j=>j.action==='preflight'&&(j.result?.reports?.some(r=>r.segment_id===segment?.id)||j.segment_id===segment?.id));
 const report=pre?.result?.reports?.find(r=>r.segment_id===segment?.id);
 const estimate=report?.estimate||pre?.result?.estimate||pre?.result?.preflight?.estimate;
 const totalReserve=pre?.result?.total_reserved_yuan;
 const reservation=estimate?.reserved_yuan,numberBudget=Number(budget);
 const submitting=jobs.some(j=>j.action==='submit'&&['QUEUED','RUNNING','UNKNOWN'].includes(j.status));
 const existingTask=segment?.task?.task_id||segment?.task_id||segment?.wan_task_id;
 const ready=!submitting&&!existingTask&&pre?.status==='SUCCEEDED'&&!pre?.result?.blockers?.length&&segment?.reviewed&&Number.isFinite(numberBudget)&&numberBudget>0&&config?.configured;
 const set=(key,value)=>onPatch({settings:{...params,[key]:value,watermark:false,audio:false}});
 return <aside className="inspector"><section><h2>输出设置</h2><Field label="视频模型"><NativeSelect value="wan3.0-video" aria-label="视频模型" disabled><NativeSelectOption value="wan3.0-video">万相 3.0</NativeSelectOption></NativeSelect></Field><Field label="分辨率"><NativeSelect value={params.resolution||'480P'} onChange={e=>set('resolution',e.target.value)}><NativeSelectOption value="1080P">1080P (1920 × 1080)</NativeSelectOption><NativeSelectOption value="720P">720P</NativeSelectOption><NativeSelectOption value="480P">480P</NativeSelectOption></NativeSelect></Field><Field label="画面比例"><NativeSelect value={params.ratio||'16:9'} onChange={e=>set('ratio',e.target.value)}><NativeSelectOption>16:9</NativeSelectOption><NativeSelectOption>9:16</NativeSelectOption><NativeSelectOption>1:1</NativeSelectOption><NativeSelectOption>4:3</NativeSelectOption><NativeSelectOption>adaptive</NativeSelectOption></NativeSelect></Field><div className="inspector-field"><span>水印</span><span className="fixed-setting"><Switch checked={false} disabled aria-label="水印关闭" />关闭</span></div><div className="inspector-field"><span>声音后期</span><strong className="fixed-value">接入原片音乐</strong></div><p className="field-help">视频生成关闭声音，成片后统一对齐原音轨。</p></section><section className="preflight-panel"><h3>生成前检查</h3><h2>先检查，再生成</h2><p>核对片段范围、素材引用、人物结构、提示词与费用。</p><div className="quote"><span>{segment?.name||'当前片段'}预估</span><strong>{estimate?`¥${Number(estimate.estimate_yuan).toFixed(2)}`:'尚未检查'}</strong>{estimate&&<small>保守预留 ¥{Number(reservation).toFixed(2)} · 输入 + 输出计费</small>}</div>{Number.isFinite(totalReserve)&&<p className="total-reserve">本次检查合计预留 <strong>¥{totalReserve.toFixed(2)}</strong></p>}<Button className="w-full" variant="default" onClick={()=>onAction('preflight')} disabled={!segment} busy={busy==='preflight'}><Search size={17} />检查素材</Button><UiButton type="button" variant="link" className="h-auto justify-start px-0" onClick={()=>onAction('preflight',{segment_id:null})}>检查全部片段与合计费用</UiButton><div className="authorization"><Field label="生成操作"><NativeSelect value={auth.mode||'manual'} onChange={e=>onPatch({authorization:{...auth,mode:e.target.value}})}><NativeSelectOption value="manual">由我点击生成</NativeSelectOption><NativeSelectOption value="agent">授权代理在额度内生成</NativeSelectOption></NativeSelect></Field><Field label="本项目总授权额度 / 元"><Input type="number" min="0" step="0.01" value={budget} onChange={e=>setBudget(e.target.value)} placeholder="填写总费用上限"/></Field><Button className="w-full" onClick={()=>onPatch({authorization:{...auth,budget_yuan:Number(budget)}})} disabled={!Number.isFinite(numberBudget)||numberBudget<=0}><Save size={15} />保存授权额度</Button>{auth.mode==='agent'&&<small>代理可在已保存额度内提交；状态未知时不会重复生成。</small>}</div><Button className="w-full" onClick={()=>onAction('submit',{budget_yuan:Number(auth.budget_yuan),operator:'user'})} disabled={true} busy={busy==='submit'}><Play size={17} fill="currentColor" />{existingTask?'已有生成任务':submitting?'提交任务处理中':'等待确认后上传并生成'}</Button>{!config?.configured&&<UiButton type="button" variant="link" className="config-link h-auto justify-start px-0" onClick={onConfig}>配置百炼 API Key <ChevronRight size={14} /></UiButton>}<p className="small-note">点击生成会调用付费 API。费用以百炼实际账单为准，检查不产生生成费用。</p></section><section><h3>导出成片</h3><p>生成完成后，去除分段重叠并接入连续音乐；检查触球与节拍是否对齐。</p><Button className="w-full" onClick={()=>onStep(4)}>前往拼接与音乐<ArrowRight size={16} /></Button></section></aside>;
}

const ENVIRONMENT_LABELS={ffmpeg:'本机视频处理',generation_api:'生成接口类型',blender:'Blender（历史项目）',pose_model:'姿态模型（历史项目）',music_separation:'背景音乐分离（可选）',python:'Python',version:'工具版本'};

export function ConfigDrawer({config,onClose,onSave,system,error,returnFocus}){
 const [key,setKey]=useState(''),[base,setBase]=useState(config?.base_url||''),[workspace,setWorkspace]=useState(config?.workspace_id||''),[persist,setPersist]=useState(false),[busy,setBusy]=useState(false);
 const fieldId=useId();
 const environment=Object.entries(ENVIRONMENT_LABELS).filter(([name])=>system?.[name]!==undefined&&system[name]!==null&&typeof system[name]!=='object');
 const workspaceHint='填写密钥所属的业务空间。使用空间专属接口时，请填写对应的官方地址，或将下方地址留空以按此空间连接北京接口。';
 const addressHint='接口地域须与密钥和业务空间一致。留空时，有 Workspace ID 使用该空间北京地址；未填写空间时使用北京默认接口。';
 const connectionNote='仅连接阿里云百炼官方接口，密钥留在本机后端。保存只更新连接配置，生成需在第 4 步另行确认材料与本次费用。';
 async function submit(event){
  event.preventDefault();
  setBusy(true);
  try{await onSave({...(key?{api_key:key}:{}),base_url:base.trim(),workspace_id:workspace.trim(),persist_key:persist});setKey('');}
  finally{setBusy(false);}
 }
 return <Dialog open onOpenChange={open=>{if(!open)onClose();}}>
  <DialogContent className="generation-config-dialog" onCloseAutoFocus={event=>{
    const target=returnFocus?.current;
    if(target?.isConnected){event.preventDefault();target.focus({preventScroll:true});}
   }} showCloseButton={false} style={{ maxWidth: "640px" }}>
   
    <header className="generation-config-heading flex items-center justify-between gap-4">
     <DialogTitle>API 设置</DialogTitle>
     <DialogClose asChild><UiButton type="button" aria-label="关闭设置" variant="ghost" size="icon"><X size={20} aria-hidden="true" /></UiButton></DialogClose>
    </header>
   
   <div className="flex flex-col gap-6 mt-6">
    <DialogDescription>{connectionNote}</DialogDescription>
    <Alert>
     <ShieldCheck size={18} aria-hidden="true" />
     <AlertDescription>
      <span className="text-sm font-bold">{config?.configured?'百炼密钥已配置':'尚未配置百炼密钥'}</span>
      <p className="text-sm mb-0">{config?.source||'可填写新密钥，留空时沿用已有配置。'}</p>
      <p className="text-sm mb-0">已配置表示本机存在密钥。实际请求仍会核对鉴权、权限和账户额度。</p>
     </AlertDescription>
    </Alert>
    {error&&<Alert role="alert" variant="destructive"><AlertCircle size={16} aria-hidden="true" /><AlertDescription>{error}</AlertDescription></Alert>}
    <form onSubmit={submit}>
     <FieldGroup className="gap-4">
      <FormField className="generation-field">
       <FieldLabel htmlFor={fieldId+'-key'}>百炼 API Key</FieldLabel>
       <Input id={fieldId+'-key'} type="password" autoComplete="new-password" value={key} onChange={event=>setKey(event.target.value)} placeholder={config?.configured?'已配置，留空沿用现有密钥':'在本机填写，不要发到聊天中'} />
      </FormField>
      <FormField className="generation-field">
       <FieldLabel htmlFor={fieldId+'-workspace'}>Workspace ID（可选）</FieldLabel>
       <Input id={fieldId+'-workspace'} value={workspace} onChange={event=>setWorkspace(event.target.value)} placeholder="业务空间 ID" aria-describedby={fieldId+'-workspace-hint'} />
       <FieldDescription id={fieldId+'-workspace-hint'}>{workspaceHint}</FieldDescription>
      </FormField>
      <FormField className="generation-field">
       <FieldLabel htmlFor={fieldId+'-url'}>官方 API 地址</FieldLabel>
       <Input id={fieldId+'-url'} type="url" value={base} onChange={event=>setBase(event.target.value)} placeholder="https://dashscope.aliyuncs.com/api/v1" aria-describedby={fieldId+'-url-hint'} />
       <FieldDescription id={fieldId+'-url-hint'}>{addressHint}</FieldDescription>
      </FormField>
      <FormField orientation="horizontal" className="generation-config-confirm">
       <Checkbox id={fieldId+'-persist'} checked={persist} onCheckedChange={checked=>setPersist(checked===true)} />
       <FieldLabel htmlFor={fieldId+'-persist'}>将新密钥保存到本机私有配置文件</FieldLabel>
      </FormField>
      <div className="flex justify-end"><UiButton type="submit" size="lg" disabled={(busy)} aria-busy={(busy) || undefined}>{(busy) && <Spinner />}保存 API 设置</UiButton></div>
     </FieldGroup>
    </form>
    <section aria-labelledby={fieldId+'-environment'}>
     <h3 id={fieldId+'-environment'} className="font-semibold tracking-tight text-lg mb-2">本机处理环境</h3>
     <dl className="generation-config-environment">{environment.map(([name,label])=><div key={name}>
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-sm font-medium">{typeof system[name]==='boolean'?(system[name]?'可用':'未安装'):String(system[name])}</dd>
     </div>)}</dl>
    </section>
   </div>
  </DialogContent>
 </Dialog>;
}
