import React, { useEffect, useId, useRef, useState } from 'react';
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from "@/components/ui/select";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
import { AlertCircle, ArrowRight, ChevronDown, FileVideo, FolderInput, PanelRightClose, PanelRightOpen, Plus, Save, Trash2, Upload, X } from 'lucide-react';
import { time } from '../api';
import useDraft from '../useDraft';
import { aspectRatio, DeliveryDimensionNote, specSeconds } from './DeliverySpec';
import { sourceRangePreviewModel } from '../sourceRangePreviewModel';
import SourceRangePlayer from './SourceRangePlayer';
import './SourceWorkspace.css';

const DELIVERY_TIERS = ['480P', '720P', '1080P'];

function SourceTimeInput({ value, ...props }) {
  const [editing, setEditing] = useState(false);
  // Only the unfocused display is rounded. Drafts keep their original precision.
  const rounded = Number.isFinite(value) ? Number(value.toFixed(3)) : value;
  const display = editing ? value : value <= props.max && rounded > props.max
    ? Math.floor(value * 1000) / 1000 : rounded;
  return <Input {...props} className="source-time-input" type="number" value={display} onFocus={() => setEditing(true)} onBlur={() => setEditing(false)} />;
}

function SourceUpload({ busy, onUpload }) {
  const fileInput = useRef(null);
  const [uploading, setUploading] = useState(false);

  async function chooseFile(event) {
    const input = event.currentTarget;
    const files = [...input.files];
    if (!files.length) return;
    setUploading(true);
    try { await onUpload('source', files); }
    finally {
      input.value = '';
      setUploading(false);
    }
  }

  return (
    <>
      <input ref={fileInput} type="file" hidden accept="video/mp4,video/quicktime" onChange={chooseFile}
        disabled={busy || uploading} />
      <Button type="button" onClick={() => fileInput.current?.click()} size="lg" disabled={(uploading) || (busy)} aria-busy={(uploading) || undefined}>{(uploading) && <Spinner />}
        <Upload size={17} aria-hidden="true" />从电脑选择原片
      </Button>
    </>
  );
}

function SourcePathImport({ busy, onImport }) {
  const fieldId = useId();
  const trigger = useRef(null);
  const [open, setOpen] = useState(false);
  const [path, setPath] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  async function submit(event) {
    event.preventDefault();
    const value = path.trim();
    if (!value || pending || busy) return;
    setPending(true);
    setError('');
    try {
      const result = await onImport({ path: value, kind: 'source' });
      if (result === null) {
        setError('文件未能导入。请查看页面中的错误说明，修正路径后重试。');
        return;
      }
      setPath('');
      close();
    } catch (err) {
      setError(err.message || '文件未能导入，请检查路径后重试。');
    } finally {
      setPending(false);
    }
  }

  return (
    <Collapsible className="source-import-options" open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <Button ref={trigger} type="button" className="source-disclosure-trigger" variant="outline" size="lg" disabled={busy || pending}>
          <FolderInput size={17} aria-hidden="true" />通过本机文件路径导入
          <ChevronDown size={16} className="source-disclosure-arrow" aria-hidden="true" />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="source-expand-content">
        <form onSubmit={submit} className="source-path-form">
          <Field className="source-range-field">
            <FieldLabel htmlFor={fieldId}>原片绝对路径</FieldLabel>
            <Input id={fieldId} value={path} onChange={event => { setPath(event.target.value); setError(''); }} placeholder="例如：/Users/你的名字/Movies/视频.mp4" required disabled={busy || pending} aria-describedby={`${fieldId}-hint`} />
          </Field>
          <div className="source-file-actions">
            <Button type="submit" size="lg" disabled={(pending) || (busy || !path.trim())} aria-busy={(pending) || undefined}>{(pending) && <Spinner />}导入此文件</Button>
            <Button type="button" aria-label="取消导入" onClick={close} variant="outline" size="icon" disabled={busy || pending}><X size={18} aria-hidden="true" /></Button>
          </div>
        </form>
        <p id={`${fieldId}-hint`} className="text-sm text-muted-foreground">原文件留在原处。导入后请保留文件及其位置。</p>
        {error && <Alert role="alert" variant="destructive">
          <AlertCircle size={16} aria-hidden="true" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>}
      </CollapsibleContent>
    </Collapsible>
  );
}

function SourceFacts({ source, duration, deliverySpec }) {
  const frames = source.frame_count ?? deliverySpec?.source_frame_count;
  const [numerator, denominator = 1] = String(source.fps_rational || source.fps || '').split('/').map(Number);
  const fps = denominator > 0 && numerator > 0 ? Number((numerator / denominator).toFixed(3)) : null;
  const seconds = deliverySpec?.source_duration_seconds ?? source.video_duration_seconds ?? duration;
  const facts = [
    ['原片画幅', aspectRatio(source.width, source.height)],
    ['原片宽高', source.width && source.height ? `${source.width} × ${source.height}` : '待检查'],
    ['原片帧率', fps ? `${fps} 帧/秒` : '待检查'],
    ['原片帧数', Number.isInteger(frames) ? `${frames} 帧` : '待检查'],
    ['画面时长', Number.isFinite(seconds) ? `${Number(seconds.toFixed(3))} 秒` : '待检查'],
    ['声音', source.has_audio ? '含原声' : '无音轨'],
  ];
  return <dl className="source-facts text-sm">{facts.map(([label, value]) => (
    <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="font-medium tabular-nums">{value}</dd></div>
  ))}</dl>;
}

function SourceWorkingPreview({ project, segments, selectedId, onSelect, specAction }) {
  const [mode, setMode] = useState('range');
  const segment = segments.find(item => item.id === selectedId) || segments[0];
  const savedSegment = project.segments?.find(item => item.id === segment?.id);
  const playbackMode = segment ? mode : 'original';
  const model = sourceRangePreviewModel(project, segment, savedSegment, playbackMode);
  const hasRangeChoice = segment && (segments.length > 1 || segment.start !== 0 ||
    segment.end !== model.sourceDuration || !model.rangeValid);

  return (
    <section className="source-section source-inline-preview" aria-labelledby="source-preview-title">
      <Tabs value={playbackMode} onValueChange={setMode} className="gap-5">
        <div className="source-preview-heading">
          <div className="min-w-0">
            <h2 id="source-preview-title" className="text-lg font-semibold">原片预览</h2>
            <p className="source-preview-file text-sm text-muted-foreground" title={project.source.name}>{project.source.name}</p>
          </div>
          <div className="source-preview-view-tools">
            {hasRangeChoice && <TabsList aria-label="工作区原片播放范围">
              <TabsTrigger value="original">完整原片</TabsTrigger>
              <TabsTrigger value="range">当前分段</TabsTrigger>
            </TabsList>}
            {specAction}
          </div>
        </div>
        {hasRangeChoice ? ['original', 'range'].map(value => <TabsContent key={value} value={value} className="mt-0">
          {playbackMode === value && <SourceRangePlayer key={model.identity} model={model} sourcePath={project.source.path} />}
        </TabsContent>) : <SourceRangePlayer key={model.identity} model={model} sourcePath={project.source.path} />}
        <div className="source-inline-selection">
          {segments.length > 1 && <Select value={segment.id} onValueChange={id => { onSelect(id); setMode('range'); }}>
            <SelectTrigger aria-label="工作区预览片段"><SelectValue>{segment.name || `片段 ${segments.indexOf(segment) + 1}`}</SelectValue></SelectTrigger>
            <SelectContent position="popper" className="source-select-options"><SelectGroup>
              {segments.map((item, index) => <SelectItem key={item.id} value={item.id}>{item.name || `片段 ${index + 1}`}</SelectItem>)}
            </SelectGroup></SelectContent>
          </Select>}
          <span role="status" className="text-sm text-muted-foreground">
            {model.full ? `完整原片 · ${time(model.sourceDuration)}` : model.rangeValid
              ? `${time(segment.start)} — ${time(segment.end)}` : '请先修正分段起止时间'}
          </span>
          {!model.full && model.rangeChanged && <Badge variant="warning">未保存范围</Badge>}
        </div>
      </Tabs>
    </section>
  );
}

function SourceDeliveryFacts({ spec }) {
  const facts = [
    ['目标宽高', spec?.width && spec?.height ? `${spec.width} × ${spec.height}` : '待核实'],
  ];
  return <dl className="source-facts source-spec-summary text-sm">{facts.map(([label, value]) =>
    <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="font-medium tabular-nums">{value}</dd></div>
  )}</dl>;
}

export function GenericSourceStage({
  project, deliverySpec, busy = false, onPatch, onUpload, onImport, onNext, selectedId, onSelectSegment,
  fileDetailsRequest, navigationRequest,
}) {
  const [segments, set, dirty, save, reset] = useDraft(project.segments || [], project.id + 'segments', project.revision);
  const [fileDetailsOpen, setFileDetailsOpen] = useState(false);
  const [specOpen, setSpecOpen] = useState(() => typeof window === 'undefined' || !window.matchMedia
    ? true : window.matchMedia('(min-width: 1280px)').matches);
  const [previewId, setPreviewId] = useState(null);
  const specToggle = useRef(null);
  const specPanel = useRef(null);
  const segmentFieldPrefix = useId();
  useEffect(() => {
    setFileDetailsOpen(false);
    setPreviewId(null);
  }, [project.id]);
  useEffect(() => {
    if (fileDetailsRequest) {
      setFileDetailsOpen(true);
      if (project.source) setSpecOpen(true);
    }
  }, [fileDetailsRequest, project.id, project.source?.id]);
  useEffect(() => {
    const query = window.matchMedia?.('(min-width: 1280px)');
    if (!query) return;
    const change = event => {
      if (event.matches) setSpecOpen(true);
      else closeSpecs();
    };
    query.addEventListener('change', change);
    return () => query.removeEventListener('change', change);
  }, []);
  useEffect(() => {
    const targetId = navigationRequest?.targetId;
    if (!['source-import', 'delivery-spec'].includes(targetId) || !project.source) return;
    setSpecOpen(true);
    if (targetId === 'source-import') setFileDetailsOpen(true);
    const frame = requestAnimationFrame(() => {
      const target = document.getElementById(targetId);
      target?.scrollIntoView({ block: 'nearest' });
      target?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [navigationRequest, project.id, project.source?.id]);

  const source = project.source_meta || {};
  const settings = project.settings || {};
  const duration = source.duration || 0;
  const tier = settings.resolution || '';
  const valid = segments.length > 0 && segments.every(segment => (
    Number.isFinite(segment.start) && Number.isFinite(segment.end) &&
    segment.start >= 0 && segment.end > segment.start &&
    segment.end - segment.start <= 15.00001 && segment.end <= duration + .001
  ));
  const lastEnd = segments.at(-1)?.end || 0;

  function editSegment(id, field, value) {
    set(list => list.map(segment => segment.id === id ? { ...segment, [field]: value } : segment));
  }

  function addSegment() {
    const id = `segment_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
    set(list => [...list, {
      id, name: `片段 ${list.length + 1}`, start: lastEnd,
      end: Math.min(lastEnd + 14.5, duration), status: 'NEEDS_REVIEW',
    }]);
  }

  function selectPreview(id) {
    setPreviewId(id);
    if (project.segments?.some(segment => segment.id === id)) onSelectSegment?.(id);
  }

  function closeSpecs() {
    if (specPanel.current?.contains(document.activeElement)) specToggle.current?.focus({ preventScroll: true });
    setSpecOpen(false);
  }

  function toggleSpecs() {
    if (specOpen) { closeSpecs(); return; }
    setSpecOpen(true);
    if (window.matchMedia?.('(max-width: 1279px)').matches) {
      requestAnimationFrame(() => {
        specPanel.current?.scrollIntoView({ block: 'start' });
        specPanel.current?.focus({ preventScroll: true });
      });
    }
  }

  const saveSegments = () => save(onPatch, { segments });

  const specAction = <Button ref={specToggle} type="button" aria-label={specOpen ? '收起规格' : '查看规格与交付'}
    title={specOpen ? '收起规格' : '查看规格与交付'} aria-expanded={specOpen} aria-controls="source-spec-panel"
    onClick={toggleSpecs} variant="ghost" className="xl:size-9 xl:p-0">
    {specOpen ? <PanelRightClose aria-hidden="true" /> : <PanelRightOpen aria-hidden="true" />}
    <span className="xl:hidden">交付规格</span>
  </Button>;

  return (
    <div className="stage-body source-stage" data-testid="source-stage">
      <div className="source-work-grid" data-spec-open={!!project.source && specOpen}>
        <div className="source-main">
          {!project.source && <div id="source-segments" tabIndex={-1}>
            <section id="source-import" className="source-section" aria-labelledby="source-file-title" tabIndex={-1}>
              <Card className="source-panel">
                <CardHeader>
                  <CardTitle className="source-file-identity"><FileVideo aria-hidden="true" /><h2 id="source-file-title" className="text-lg">导入原视频</h2></CardTitle>
                  <CardDescription>选择本机视频，开始设置分段。</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col items-start gap-5">
                  <SourceUpload busy={busy} onUpload={onUpload} />
                  <SourcePathImport busy={busy} onImport={onImport} />
                </CardContent>
              </Card>
            </section>
          </div>}

          {project.source && <>
            <SourceWorkingPreview project={project} segments={segments} selectedId={previewId || selectedId}
              onSelect={selectPreview} specAction={specAction} />
            <section id="source-segments" className="source-section" aria-labelledby="source-ranges-title" tabIndex={-1}>
              <Card className="source-panel">
                <CardHeader>
                  <CardTitle><h2 id="source-ranges-title" className="text-lg">分段范围</h2></CardTitle>
                  <CardDescription>每段最多 15 秒。</CardDescription>
                  <CardAction><Button type="button" onClick={addSegment} variant="outline" disabled={lastEnd >= duration || busy}>
                    <Plus data-icon="inline-start" aria-hidden="true" />添加分段
                  </Button></CardAction>
                </CardHeader>
                <CardContent>
                  {segments.length ? <div className="source-ranges">{segments.map((segment, index) => (
                    <FieldGroup className="source-range-row" key={segment.id}>
                      <Field className="source-range-field source-range-name">
                        <FieldLabel htmlFor={`${segmentFieldPrefix}-${segment.id}`}>片段 {index + 1}</FieldLabel>
                        <Input id={`${segmentFieldPrefix}-${segment.id}`} aria-label={`${segment.id}片段名称`} value={segment.name || segment.id} onChange={event => editSegment(segment.id, 'name', event.target.value)} />
                      </Field>
                      {['start', 'end'].map(field => {
                        const label = field === 'start' ? '起点' : '终点';
                        const id = `${segmentFieldPrefix}-${segment.id}-${field}`;
                        return <Field className="source-range-field" key={field}>
                          <FieldLabel htmlFor={id}>{label} / 秒</FieldLabel>
                          <SourceTimeInput id={id} aria-label={`${segment.id}原片${label}秒`} min="0" max={duration} step="0.001" value={segment[field]} onChange={event => editSegment(segment.id, field, Number(event.target.value))} title={`原始时间：${segment[field]} 秒。默认显示到毫秒，编辑时显示原始精度。`} />
                        </Field>;
                      })}
                      <div className="source-range-field source-range-duration">
                        <span className="text-sm text-muted-foreground">时长</span><strong className="text-sm font-medium">{(segment.end - segment.start).toFixed(2)} 秒</strong>
                      </div>
                      <div className="source-range-actions">
                        <Button type="button" aria-label={`删除${segment.name || segment.id}片段`} title="删除片段" onClick={() => set(list => list.filter(item => item.id !== segment.id))} variant="ghost" size="icon-sm"><Trash2 aria-hidden="true" /></Button>
                      </div>
                    </FieldGroup>
                  ))}</div> : <p className="source-ranges-empty text-sm text-muted-foreground">添加分段，设置起止时间。</p>}
                  {dirty && !valid && <Alert role="alert" variant="destructive"><AlertCircle aria-hidden="true" /><AlertDescription>请检查范围：每段大于 0 秒且不超过 15 秒，起点和终点须在原片范围内。</AlertDescription></Alert>}
                  <div className="source-range-footer">
                    <div className="source-range-save-state">
                      <span role="status" className={dirty ? 'text-sm text-warning' : 'sr-only'}>{dirty ? '有未保存修改' : segments.length ? '分段范围已保存' : '尚未设置分段'}</span>
                      {dirty && <Button type="button" onClick={reset} variant="ghost">放弃修改</Button>}
                    </div>
                    <div className="source-range-actions">
                      {dirty && <Button type="button" onClick={saveSegments} data-testid="save-source-segments" disabled={!valid || busy}><Save data-icon="inline-start" aria-hidden="true" />保存分段范围</Button>}
                      {onNext && <Button type="button" onClick={onNext} title={dirty ? '切换步骤不会自动保存。未保存的修改可返回继续编辑。' : undefined} variant={dirty ? 'outline' : 'default'}>下一步：人物替换<ArrowRight data-icon="inline-end" aria-hidden="true" /></Button>}
                    </div>
                  </div>
                </CardContent>
              </Card>
            </section>
          </>}
        </div>

        {project.source && <aside id="source-spec-panel" ref={specPanel} className="source-spec-panel" hidden={!specOpen} aria-label="规格与交付" tabIndex={-1}>
          <div className="source-spec-sections">
            <section id="delivery-spec" className="source-section source-spec-section" aria-label="本批交付规格" tabIndex={-1}>
              <div className="source-spec-heading">
                <h2 className="text-lg font-semibold">交付规格</h2>
                <Button type="button" className="xl:hidden" aria-label="收起规格与交付" onClick={closeSpecs} variant="ghost" size="icon"><PanelRightClose aria-hidden="true" /></Button>
              </div>
              <Field className="source-tier">
                <FieldLabel className="sr-only" htmlFor={`${segmentFieldPrefix}-tier`}>交付档位</FieldLabel>
                <Select value={tier} disabled={busy} onValueChange={value => onPatch({ settings: { ...settings, resolution: value } })}>
                  <SelectTrigger id={`${segmentFieldPrefix}-tier`} aria-label="交付档位" className="w-full"><SelectValue placeholder="选择档位">{tier || undefined}</SelectValue></SelectTrigger>
                  <SelectContent position="popper" className="source-select-options"><SelectGroup>{DELIVERY_TIERS.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectGroup></SelectContent>
                </Select>
              </Field>
              <SourceDeliveryFacts spec={deliverySpec} />
              <p className="text-sm text-muted-foreground">档位自动保存；更改后需重新准备、报价并确认。</p>
              {deliverySpec?.dimension_basis === 'unverified' && <DeliveryDimensionNote spec={deliverySpec} />}
              {deliverySpec?.model_output_fps && deliverySpec.model_output_fps !== deliverySpec.fps && <p className="text-sm text-warning">模型输出 {deliverySpec.model_output_fps} 帧/秒，与交付帧率不同，需在回片时核对。</p>}
              {deliverySpec?.coverage_verified === false && Number.isInteger(deliverySpec.frame_count) && <p className="text-sm text-muted-foreground">帧数与覆盖暂按元数据计算，报价时解码核对。</p>}
              {dirty && <p className="text-sm text-warning">规格按已保存分段计算。</p>}
              <Collapsible className="source-technical-details group/disclosure">
                <CollapsibleTrigger asChild><Button type="button" variant="ghost" className="h-auto w-[calc(100%+1.5rem)] -mx-3 justify-between whitespace-normal px-3 py-2 text-start has-[>svg]:px-3">交付参数<ChevronDown aria-hidden="true" className="group-data-[state=open]/disclosure:rotate-180" /></Button></CollapsibleTrigger>
                <CollapsibleContent forceMount className="source-technical-content">
                  <dl className="source-facts text-sm">
                    {deliverySpec?.model && <div><dt className="text-muted-foreground">生成模型</dt><dd className="font-medium tabular-nums">{deliverySpec.model}</dd></div>}
                    {deliverySpec?.ratio && <div><dt className="text-muted-foreground">画幅</dt><dd className="font-medium tabular-nums">{deliverySpec.ratio}</dd></div>}
                    {deliverySpec?.fps && <div><dt className="text-muted-foreground">交付帧率</dt><dd className="font-medium tabular-nums">{deliverySpec.fps} 帧/秒</dd></div>}
                    <div><dt className="text-muted-foreground">完整帧数</dt><dd className="font-medium tabular-nums">{Number.isInteger(deliverySpec?.frame_count) ? `${deliverySpec.frame_count} 帧` : '报价时核对'}</dd></div>
                    <div><dt className="text-muted-foreground">画面覆盖</dt><dd className="font-medium tabular-nums">{specSeconds(deliverySpec?.duration_seconds)}</dd></div>
                    {deliverySpec?.request_ratio && deliverySpec.request_ratio !== deliverySpec.ratio && <div><dt className="text-muted-foreground">请求画幅</dt><dd className="font-medium tabular-nums">{deliverySpec.request_ratio === 'adaptive' ? '自适应' : deliverySpec.request_ratio}</dd></div>}
                  </dl>
                  {deliverySpec?.dimension_basis !== 'unverified' && <DeliveryDimensionNote spec={deliverySpec} />}
                  <p className="text-sm text-muted-foreground">每批使用一个档位，多档需分别报价、生成与验收。</p>
                </CollapsibleContent>
              </Collapsible>
            </section>
            <Separator />
            <section id="source-import" className="source-section source-spec-section" aria-labelledby="source-file-title" tabIndex={-1}>
              <Collapsible open={fileDetailsOpen} onOpenChange={setFileDetailsOpen} className="source-technical-details group/disclosure">
                <h2 id="source-file-title"><CollapsibleTrigger asChild><Button type="button" className="h-auto w-[calc(100%+1.5rem)] -mx-3 justify-between whitespace-normal px-3 py-2 text-start has-[>svg]:px-3" variant="ghost">原片信息<ChevronDown aria-hidden="true" className="group-data-[state=open]/disclosure:rotate-180" /></Button></CollapsibleTrigger></h2>
                <CollapsibleContent forceMount className="source-technical-content">
                  <SourceFacts source={source} duration={duration} deliverySpec={deliverySpec} />
                  <p className="text-sm text-muted-foreground">更换原片需新建项目。</p>
                </CollapsibleContent>
              </Collapsible>
            </section>
          </div>
        </aside>}
      </div>
    </div>
  );
}
