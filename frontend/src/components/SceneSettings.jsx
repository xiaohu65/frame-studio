import { Collapsible as CollapsibleRoot, CollapsibleTrigger, CollapsibleContent } from './ui/collapsible';
import { Empty, EmptyHeader, EmptyDescription } from './ui/empty';
import React, { useEffect, useId, useState } from 'react';

import { Card, CardAction, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { Field, FieldDescription, FieldGroup, FieldLabel } from './ui/field';
import { Input } from './ui/input';
import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from './ui/select';
import { Checkbox } from './ui/checkbox';
import { Button as UiButton } from './ui/button';
import { Textarea } from './ui/textarea';
import { Separator } from './ui/separator';
import { ChevronDown, Plus, Save, Trash2 } from 'lucide-react';
import { assetUrl } from '../api';
import useDraft from '../useDraft';
import SceneFileImport from './SceneFileImport';
import './SceneSettings.css';

const COORDINATES = ['左 x1', '上 y1', '右 x2', '下 y2'];
const EMPTY_SCENE = { mode: 'preserve', description: '', regions: [], images: [] };
const REFERENCE_TYPES = 'image/png,image/jpeg,image/webp,image/bmp';

function SceneField({ id, label, children, disabled }) {
  return (
    <Field className="scene-setting-field gap-2" data-disabled={disabled || undefined}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      {children}
    </Field>
  );
}

function SceneRegion({ region, index, segments, busy, onEdit, onSelect, onRemove }) {
  const id = useId();
  const label = `场景范围${index + 1}`;
  const segmentId = segments.some(segment => segment.id === region.segment_id) ? region.segment_id : '';

  return (
    <section className="scene-setting-region" aria-labelledby={`${id}-title`}>
      <header className="scene-setting-region-heading">
        <h4 id={`${id}-title`} className="text-sm font-medium">范围 {index + 1}</h4>
        <div className="flex items-center gap-2">
          <Badge variant={region.confirmed ? 'secondary' : 'warning'}>
            {region.confirmed ? '已确认' : '待确认'}
          </Badge>
          <UiButton type="button" disabled={busy} onClick={onRemove} aria-label={`删除场景范围 ${index + 1}`} title="删除范围" variant="ghost" size="icon-sm">
            <Trash2 aria-hidden="true" data-icon="inline-start" />
          </UiButton>
        </div>
      </header>
      <div className="scene-setting-region-content">
        <FieldGroup className="scene-setting-region-fields">
          <SceneField id={`${id}-name`} label="名称" disabled={busy}>
            <Input id={`${id}-name`} aria-label={`${label}名称`} value={region.label} disabled={busy} onChange={event => onEdit('label', event.target.value)} />
          </SceneField>
          <SceneField id={`${id}-segment`} label="所属片段" disabled={busy || !segments.length}>
            <Select value={segmentId} disabled={busy || !segments.length} onValueChange={onSelect}>
              <SelectTrigger id={`${id}-segment`} aria-label={`${label}片段`}><SelectValue placeholder="选择片段">{segments.find(item => item.id === segmentId)?.name || segmentId || undefined}</SelectValue></SelectTrigger>
              <SelectContent position="popper"><SelectGroup>
                {segments.map(segment => (
                  <SelectItem key={segment.id} value={segment.id}>{segment.name || segment.id}</SelectItem>
                ))}
              </SelectGroup></SelectContent>
            </Select>
          </SceneField>
          {['start', 'end'].map((key, fieldIndex) => (
            <SceneField key={key} id={`${id}-${key}`} label={`${fieldIndex ? '终点' : '起点'} / 秒`} disabled={busy}>
              <Input id={`${id}-${key}`} aria-label={`${label}${fieldIndex ? '终点' : '起点'}秒`} type="number" step="0.001" value={region[key]} disabled={busy} onChange={event => onEdit(key, Number(event.target.value))} />
            </SceneField>
          ))}
        </FieldGroup>
        <FieldGroup className="scene-setting-coordinate-fields">
          {COORDINATES.map((coordinate, coordinateIndex) => (
            <SceneField key={coordinate} id={`${id}-coordinate-${coordinateIndex}`} label={coordinate} disabled={busy}>
              <Input id={`${id}-coordinate-${coordinateIndex}`} aria-label={`${label}${coordinate}`} type="number" min="0" max="1" step="0.001" value={region.box[coordinateIndex]} disabled={busy} onChange={event => onEdit('box', region.box.map((value, valueIndex) => (
                  valueIndex === coordinateIndex ? Number(event.target.value) : value
                )))} />
            </SceneField>
          ))}
        </FieldGroup>
        <Field orientation="horizontal" className="scene-setting-confirm" data-disabled={busy || undefined}>
          <Checkbox id={`${id}-confirmed`} checked={!!region.confirmed} disabled={busy} onCheckedChange={checked => onEdit('confirmed', checked === true)} />
          <FieldLabel htmlFor={`${id}-confirmed`}>已核对范围，保留人物交互与道具位置</FieldLabel>
        </Field>
      </div>
    </section>
  );
}

function SceneReferences({ images, busy, dirty, onRemove, onUpload, onImport }) {
  const id = useId();
  return (
    <section className="scene-setting-references" aria-labelledby={id}>
      <div className="flex items-center gap-3">
        <h3 id={id} className="font-semibold">参考图</h3>
        {images.length > 0 && <Badge variant="secondary">{images.length} 张</Badge>}
      </div>
      {images.length > 0 ? <div className="scene-setting-image-grid">
        {images.map(asset => (
          <figure key={asset.id} className="scene-setting-image">
            <a href={assetUrl(asset)} target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline scene-setting-image-link">
              <img src={asset.thumbnail_url || assetUrl(asset)} alt={asset.name} loading="lazy" />
            </a>
            <figcaption className="scene-setting-image-caption">
              <span title={asset.name} className="min-w-0 truncate text-sm">{asset.name}</span>
              <UiButton type="button" disabled={busy} onClick={() => onRemove(asset.id)} aria-label={`移除${asset.name}`} title="移除参考图" variant="ghost" size="icon-sm">
                <Trash2 aria-hidden="true" data-icon="inline-start" />
              </UiButton>
            </figcaption>
          </figure>
        ))}
      </div> : <Empty className="scene-setting-reference-empty p-4 md:p-4"><EmptyHeader><EmptyDescription>暂无场景参考图。</EmptyDescription></EmptyHeader></Empty>}
      <SceneFileImport label="导入参考图" pathLabel="本机路径" size="default" kindLabel="场景参考图" accept={REFERENCE_TYPES}
        placeholder="例如：/Users/你的名字/Pictures/场景.png" multiple disabled={dirty || busy}
        onFiles={files => onUpload('reference', files, { role: 'scene' })}
        onImport={path => onImport({ path, kind: 'reference', role: 'scene' })} />
      {dirty && <span className="text-sm text-warning">保存场景设置后可导入参考图。</span>}
    </section>
  );
}

export default function SceneSettings({ project, segment, onPatch, onUpload, onImport, busy, draftTarget }) {
  const scene = project.scene || EMPTY_SCENE;
  const [draft, set, dirty, save, reset] = useDraft(scene, project.id + 'scene', project.revision);
  const [detailsOpen, setDetailsOpen] = useState(!!draftTarget?.sceneDetails);
  const id = useId();
  const regions = draft.regions || [];
  const segments = project.segments || [];

  useEffect(() => {
    if (draftTarget?.sceneDetails) setDetailsOpen(true);
  }, [draftTarget]);

  function editRegion(regionId, key, value) {
    set(current => ({
      ...current,
      regions: (current.regions || []).map(region => region.id === regionId
        ? { ...region, [key]: value, confirmed: key === 'confirmed' ? value : false }
        : region),
    }));
  }

  function selectRegionSegment(regionId, segmentId) {
    const next = segments.find(item => item.id === segmentId);
    if (!next) return;
    set(current => ({
      ...current,
      regions: current.regions.map(region => region.id === regionId
        ? { ...region, segment_id: next.id, start: next.start, end: next.end, confirmed: false }
        : region),
    }));
  }

  function addRegion() {
    if (!segment) return;
    const regionId = `scene_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`;
    set(current => ({
      ...current,
      regions: [...(current.regions || []), {
        id: regionId, segment_id: segment.id, label: `场景范围 ${(current.regions || []).length + 1}`,
        start: segment.start, end: segment.end, box: [0, 0, 1, 1], confirmed: false,
      }],
    }));
  }

  function removeRegion(regionId) {
    set(current => ({ ...current, regions: current.regions.filter(region => region.id !== regionId) }));
  }

  return (
    <Card className="scene-settings-panel" id="scene-settings" tabIndex={-1} aria-labelledby={`${id}-title`} role="region">
      <CardHeader>
        <CardTitle><h2 id={`${id}-title`}>场景处理</h2></CardTitle>
        <CardAction className="flex items-center gap-2">
          <Badge variant="secondary">全片</Badge>
          {dirty && <Badge role="status" variant="warning">未保存</Badge>}
        </CardAction>
      </CardHeader>
      <CardContent>
        <fieldset className="scene-settings-fieldset" disabled={busy}>
          <FieldGroup className="scene-settings-fields">
            <SceneField id={`${id}-mode`} label="处理方式" disabled={busy}>
              <Select value={draft.mode || 'preserve'} disabled={busy} onValueChange={mode => set(current => ({ ...current, mode }))}>
                <SelectTrigger id={`${id}-mode`} aria-label="场景处理方式"><SelectValue>{draft.mode === 'replace' ? '替换选定场景外观' : '完整保留原场景'}</SelectValue></SelectTrigger>
                <SelectContent position="popper"><SelectGroup>
                  <SelectItem value="preserve">完整保留原场景</SelectItem>
                  <SelectItem value="replace">替换选定场景外观</SelectItem>
                </SelectGroup></SelectContent>
              </Select>
              <FieldDescription>{draft.mode === 'replace'
                ? '全片共用外观；替换与保护范围按片段设置。'
                : '保留原场景；人物替换范围按片段设置。'}</FieldDescription>
            </SceneField>
            <SceneField id={`${id}-description`} label="场景说明" disabled={busy}>
              <Textarea id={`${id}-description`} aria-label="场景外观与保护说明" rows={2} value={draft.description || ''} onChange={event => set(current => ({ ...current, description: event.target.value }))} placeholder="替换的环境外观，以及需保留的人物、道具和交互区域" disabled={busy} />
            </SceneField>
          </FieldGroup>
          {draft.mode === 'replace' && <CollapsibleRoot open={detailsOpen} onOpenChange={setDetailsOpen}
            className="scene-settings-disclosure">
            <CollapsibleTrigger asChild>
              <UiButton type="button" className="scene-settings-disclosure-trigger h-auto w-[calc(100%+1.5rem)] -mx-3 justify-between whitespace-normal px-3 py-2 text-start has-[>svg]:px-3" variant="ghost">
                <span>场景定位与参考图</span>
                <ChevronDown aria-hidden="true" data-icon="inline-start" />
              </UiButton>
            </CollapsibleTrigger>
            <CollapsibleContent forceMount className="scene-settings-disclosure-body">
              <section className="scene-setting-regions" aria-labelledby={`${id}-regions`}>
                <div className="scene-setting-region-heading">
                  <h3 id={`${id}-regions`} className="font-semibold">定位范围</h3>
                  <UiButton type="button" disabled={!segment || busy} onClick={addRegion} variant="outline" size="sm">
                    <Plus aria-hidden="true" data-icon="inline-start" />添加范围
                  </UiButton>
                </div>
                {regions.map((region, index) => (
                  <React.Fragment key={region.id}>
                    {index > 0 && <Separator />}
                    <SceneRegion region={region} index={index} segments={segments} busy={busy}
                      onEdit={(key, value) => editRegion(region.id, key, value)}
                      onSelect={segmentId => selectRegionSegment(region.id, segmentId)}
                      onRemove={() => removeRegion(region.id)} />
                  </React.Fragment>
                ))}
              </section>
              <Separator />
              <SceneReferences images={scene.images || []} busy={busy} dirty={dirty} onUpload={onUpload} onImport={onImport}
                onRemove={assetId => onPatch({ scene: { ...scene, images: scene.images.filter(asset => asset.id !== assetId) } })} />
            </CollapsibleContent>
          </CollapsibleRoot>}
          {dirty && <footer className="scene-settings-save">
            <UiButton type="button" onClick={reset} variant="outline">放弃修改</UiButton>
            <UiButton type="button" disabled={!dirty || busy} onClick={() => save(onPatch, { scene: { ...draft, images: scene.images } })}>
              <Save aria-hidden="true" data-icon="inline-start" />保存场景
            </UiButton>
          </footer>}
        </fieldset>
      </CardContent>
    </Card>
  );
}
