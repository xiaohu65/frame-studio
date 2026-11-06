import {Collapsible as CollapsibleRoot,CollapsibleTrigger,CollapsibleContent} from "./ui/collapsible";
import {cn} from "@/lib/utils";
import React, { useEffect, useRef, useState } from 'react';
import { Button as UiButton } from './ui/button';

import { Badge } from './ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Alert, AlertDescription } from './ui/alert';
import { createPortal } from 'react-dom';


import { AlertCircle, ChevronDown, Download } from 'lucide-react';
import { assetUrl, time } from '../api';
import { DRAFTS } from '../draftState';
import { maskMaterialState } from '../scenePresentation';
import { sourceRangePreviewModel } from '../sourceRangePreviewModel';
import SourceRangePlayer from './SourceRangePlayer';
import { ImportedMaskEditor } from './SceneMaskEditor';
import SceneFileImport from './SceneFileImport';
import './SceneMaskPanel.css';

const MASK_ACCEPT = 'video/mp4,video/quicktime,video/x-matroska';

export function SceneSourcePreview({ project, segment }) {
  const [mode, setMode] = useState('range');
  const playbackMode = segment ? mode : 'original';
  const model = sourceRangePreviewModel(project, segment, segment, playbackMode);
  const hasRangeChoice = segment && (segment.start !== 0 || segment.end !== model.sourceDuration || !model.rangeValid);
  const player = <SourceRangePlayer key={model.identity} model={model} sourcePath={project.source?.path} />;

  return <section className="scene-reference-preview" aria-label="原片预览">
    {hasRangeChoice ? <Tabs value={playbackMode} onValueChange={setMode} className="gap-4">
      <TabsList aria-label="预览原片范围">
        <TabsTrigger value="range">当前分段</TabsTrigger>
        <TabsTrigger value="original">完整原片</TabsTrigger>
      </TabsList>
      {['range', 'original'].map(value => <TabsContent key={value} value={value}>
        {playbackMode === value && player}
      </TabsContent>)}
    </Tabs> : player}
    <p className="text-sm text-muted-foreground tabular-nums">{model.full ? `完整原片 · ${time(model.sourceDuration)}`
      : `${segment.name || segment.id} · ${time(segment.start)} — ${time(segment.end)}`}</p>
  </section>;
}

function ScopeDisclosure({ label, children, open, onOpenChange, keepMounted }) {
  return (
    <CollapsibleRoot className="scene-scope-disclosure" open={open} onOpenChange={onOpenChange}>
      <CollapsibleTrigger asChild>
        <UiButton type="button" className="scene-scope-disclosure-trigger h-auto w-[calc(100%+1.5rem)] -mx-3 justify-between whitespace-normal px-3 py-2 text-start has-[>svg]:px-3" variant="ghost">
          <span>{label}</span>
          <ChevronDown aria-hidden="true" data-icon="inline-start" />
        </UiButton>
      </CollapsibleTrigger>
      <CollapsibleContent className="scene-scope-disclosure-content"
        {...(keepMounted ? { forceMount: true, hidden: !open } : {})}>
        {children}
      </CollapsibleContent>
    </CollapsibleRoot>
  );
}

function ScopeFileDetails({ result }) {
  const href = result?.url || result?.asset?.url;
  const detail = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
  return (
    <>
      {href && <UiButton asChild className="scene-scope-download" variant="outline">
        <a href={href} download><Download aria-hidden="true" data-icon="inline-start" />查看或下载处理结果</a>
      </UiButton>}
      <ScopeDisclosure label="查看处理详情">
        <pre className="scene-scope-technical"><code>{detail}</code></pre>
      </ScopeDisclosure>
    </>
  );
}

function ScopeFiles({ files, segment, busy, onUpload, onImport }) {
  return (
    <fieldset className="scene-scope-file-list" disabled={busy}>
      {files.map(({ kind, label, state }) => (
        <section key={kind} className="scene-scope-file">
          <div className="flex items-center gap-3">
            <h3 className="text-sm font-medium scene-scope-subtitle">{label}</h3>
            {state.optional && <Badge variant={"secondary"}>可选</Badge>}
          </div>
          <div className={cn("text-sm", state.present || state.optional ? 'text-muted-foreground' : 'text-warning')}>
            {state.present ? '导入后立即切换当前文件。' : state.label}
          </div>
          <SceneFileImport label={`${state.present ? '更换' : '导入'}${label}`} kindLabel={`${segment.id}段${label}`}
            disabled={busy} accept={MASK_ACCEPT} pathLabel="路径" size="default" onFiles={items => onUpload(kind, items, { segment_id: segment.id })}
            onImport={path => onImport({ path, kind, segment_id: segment.id })} />
        </section>
      ))}
      <div className="text-sm text-muted-foreground scene-scope-note">
        原文件保留；导入后需重新生成连续预览，并核对规格与纯黑白像素。
      </div>
    </fieldset>
  );
}

function ScopeMaterials({ files, record }) {
  return (
    <ScopeDisclosure label="文件详情与制作记录">
      <div className="scene-scope-file-list">
        {files.map(({ label, state, kind }) => (
          <section key={kind} className="scene-scope-file">
            <div className="flex items-center gap-3">
              <h3 className="text-sm font-medium scene-scope-subtitle">{label}</h3>
              <Badge variant={"secondary"}>{state.present ? '已有完整文件' : state.label}</Badge>
            </div>
            {state.asset && <>
              <a href={assetUrl(state.asset)} download aria-label={`下载${state.asset.name}`} className="text-sm text-primary underline-offset-4 hover:underline break-all">{state.asset.name}</a>
              <code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-muted-foreground text-sm scene-scope-path">{state.asset.path || '保存在本机项目中'}</code>
              <ScopeFileDetails result={state.asset.meta} />
            </>}
          </section>
        ))}
        {record.present && <section className="scene-scope-file">
          <h3 className="text-sm font-medium scene-scope-subtitle">遮罩制作记录</h3>
          <div className="text-sm text-muted-foreground scene-scope-note">
            记录用于追溯；当前素材与轮廓仍需核对。
          </div>
          <ScopeFileDetails result={record.report} />
        </section>}
      </div>
    </ScopeDisclosure>
  );
}

export default function SceneMaskPanel({ project, segment, onPatch, onUpload, onImport, onAction, busy, error, draftTarget, PolygonEditor, inspectorHost }) {
  const [method, setMethod] = useState('files');
  const material = maskMaterialState(segment);
  const editPresent = material.edit.present;
  const [importOpen, setImportOpen] = useState(!editPresent);
  const appliedFileTarget = useRef(null);
  const files = [
    { kind: 'edit_mask', label: '替换遮罩', state: material.edit },
    { kind: 'protection_mask', label: '前景保护遮罩', state: material.protection },
  ];

  useEffect(() => { setMethod('files'); }, [project.id, segment?.id, segment?.edit_mask?.id]);
  useEffect(() => { setImportOpen(!editPresent); }, [editPresent]);
  useEffect(() => {
    if (draftTarget?.step !== 2 || (draftTarget.segmentId && draftTarget.segmentId !== segment?.id)) return;
    if (['edit_mask', 'protection_mask'].includes(draftTarget.maskKind)) {
      if (appliedFileTarget.current === draftTarget) return;
      appliedFileTarget.current = draftTarget;
      setMethod('files');
      setImportOpen(true);
    } else if (draftTarget.maskEditor && draftTarget.segmentId === segment?.id) {
      setMethod('draw');
    }
  }, [draftTarget, segment?.id]);

  const pendingWork = segment && (DRAFTS.has(`${project.id}mask${segment.id}`) || DRAFTS.has(`${project.id}mask-work${segment.id}`));
  const savedMaterials = segment && <dl aria-label="当前片段已保存的区域材料" className="scene-scope-status text-sm">
    <div><dt className="text-muted-foreground">替换区域</dt><dd><Badge variant={material.edit.present ? 'secondary' : 'warning'}>{material.edit.present ? '已有文件' : '待制作或导入'}</Badge></dd></div>
    <div><dt className="text-muted-foreground">前景保护</dt><dd><Badge variant="secondary">{material.protection.present ? '已有文件' : '未提供（可选）'}</Badge></dd></div>
  </dl>;
  const fileImport = segment && <div hidden={method !== 'files'}>
    <ScopeDisclosure label={editPresent ? '导入或更换文件' : '导入遮罩文件'} open={importOpen}
      onOpenChange={setImportOpen} keepMounted>
      <ScopeFiles files={files} segment={segment} busy={busy} onUpload={onUpload} onImport={onImport} />
    </ScopeDisclosure>
  </div>;

  return (
    <>
    <Card className="scene-scope-panel" id="mask-panel" data-testid="mask-panel" tabIndex={-1} aria-labelledby="scene-scope-title" role="region">
      <CardHeader><CardTitle><h2 id="scene-scope-title" className="text-lg font-semibold">替换与保护区域</h2></CardTitle></CardHeader>
      <CardContent>
        {!segment ? <div className="text-sm text-muted-foreground">请先在第 1 步保存分段。</div> : <>
          {!inspectorHost && savedMaterials}
          <Tabs value={method} onValueChange={setMethod}>
            <TabsList className="scene-scope-tabs" aria-label="区域制作方式">
              <TabsTrigger value="files">检查已有文件</TabsTrigger>
              <TabsTrigger value="draw">绘制轮廓</TabsTrigger>
            </TabsList>
            <TabsContent className="scene-scope-tab" value="files" forceMount hidden={method !== 'files'}>
              {editPresent && method === 'files' ? <ImportedMaskEditor
                key={JSON.stringify([project.id, segment.id, segment.start, segment.end, project.source?.id,
                  project.source?.sha256, segment.edit_mask?.id, segment.edit_mask?.sha256,
                  segment.protection_mask?.id, segment.protection_mask?.sha256])}
                project={project} segment={segment} onAction={onAction} busy={busy} />
                : <div className="text-sm text-muted-foreground scene-scope-note">
                  导入替换遮罩后可对照原片检查，也可切换到“绘制轮廓”制作。
                </div>}
              {!inspectorHost && fileImport}
            </TabsContent>
            <TabsContent className="scene-scope-tab" value="draw" forceMount hidden={method !== 'draw'}>
              {editPresent && <Alert data-tone={"warning"} role="note">
                <AlertCircle size={17} aria-hidden="true" />
                <AlertDescription>绘制轮廓会重建整段遮罩；局部调整已有文件请使用“检查已有文件”。</AlertDescription>
              </Alert>}
              <PolygonEditor project={project} segment={segment} onPatch={onPatch} onAction={onAction} busy={busy} error={error} />
            </TabsContent>
          </Tabs>
          {pendingWork && <Alert role="status" data-tone={"warning"}>
            <AlertCircle size={17} aria-hidden="true" />
            <AlertDescription>当前片段有未记录或未保存的手工轮廓，已有文件不包含这些修改。</AlertDescription>
          </Alert>}
          {!inspectorHost && <ScopeMaterials files={files} record={material.record} />}
        </>}
      
    </CardContent></Card>
    {segment && inspectorHost && createPortal(<>
      {savedMaterials}
      {fileImport}
      <ScopeMaterials files={files} record={material.record} />
    </>, inspectorHost)}
    </>
  );
}
