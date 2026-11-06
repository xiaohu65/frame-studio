import {Collapsible as CollapsibleRoot,CollapsibleTrigger,CollapsibleContent} from "./ui/collapsible";
import {Badge} from "./ui/badge";
import {Empty,EmptyHeader,EmptyDescription} from "./ui/empty";
import React, { useEffect, useId, useRef, useState } from 'react';
import { Button as UiButton } from './ui/button';

import { Select, SelectTrigger, SelectValue, SelectContent, SelectGroup, SelectItem } from './ui/select';
import { Input } from './ui/input';
import { Checkbox } from './ui/checkbox';


import { ChevronDown, Plus, Trash2 } from 'lucide-react';
import { assetUrl, time } from '../api';
import './PeoplePositionReview.css';

const visibilityLabels = { visible: '可见', occluded: '被遮挡', out: '已出画' };
const coordinateLabels = ['左边 x1', '上边 y1', '右边 x2', '下边 y2'];

function PositionDisclosure({ label, children, disabled, compact, title }) {
  return (
    <CollapsibleRoot disabled={disabled} className={compact ? 'position-review-disclosure-compact' : 'position-review-disclosure'}>
      <CollapsibleTrigger asChild>
        <UiButton type="button" className="position-review-disclosure-trigger h-auto w-full justify-between whitespace-normal py-2 text-start" title={title} variant={"ghost"} size={compact ? 'default' : 'lg'}>
          {label}<ChevronDown aria-hidden="true" data-icon="inline-start" />
        </UiButton>
      </CollapsibleTrigger>
      <CollapsibleContent className="position-review-disclosure-content">{children}</CollapsibleContent>
    </CollapsibleRoot>
  );
}

export default function PeoplePositionReview({
  project, actor, actorLabel, currentGroup, groups, canAdd, activeIndex, busy,
  onSelectSegment, onAdd, onSelectObservation, onRemoveObservation, onUpdateObservation,
}) {
  const segmentField = useId();
  const editorId = useId();
  const activeObservation = actor.observations?.[activeIndex];
  const showSegmentSelector = groups.length !== 1 || currentGroup?.segment.id === '__unknown__';

  function positionRow({ observation, index }) {
    const selected = index === activeIndex;
    return (
      <div className="position-review-row" key={observation.id || index}>
        <UiButton type="button" className="position-review-row-select h-auto" disabled={busy} aria-expanded={selected} aria-controls={editorId} onClick={() => onSelectObservation(observation)} variant={selected ? 'default' : 'outline'}>
          <span className="text-sm font-medium position-review-time">{time(observation.time)}</span>
          <span className="text-sm">{visibilityLabels[observation.visibility] || observation.visibility}</span>
          <Badge data-confirmed={observation.confirmed === true} variant={observation.confirmed === true ? 'success' : 'warning'}>
            {observation.confirmed === true ? '已确认' : '待确认'}
          </Badge>
        </UiButton>
        <UiButton type="button" disabled={busy} className="position-review-remove" aria-label={`删除${actorLabel}定位${index + 1}`} title="删除记录" onClick={() => onRemoveObservation(index)} variant={"ghost"} size={"icon-sm"}>
          <Trash2 aria-hidden="true" data-icon="inline-start" />
        </UiButton>
      </div>
    );
  }

  return (
    <div className="position-review">
      <div className="position-review-toolbar">
        {showSegmentSelector ? <div className="position-review-field">
          <label htmlFor={segmentField} className="text-sm font-medium">核对片段</label>
          <Select value={currentGroup?.segment.id || ''} onValueChange={onSelectSegment} disabled={busy || !groups.length}>
            <SelectTrigger id={segmentField} aria-label={`${actor.id}核对片段`}><SelectValue placeholder="暂无可核对片段">{currentGroup ? `${currentGroup.segment.name || currentGroup.segment.id} · ${currentGroup.rows.length ? `${currentGroup.pending} 处待确认` : '待添加记录'}` : undefined}</SelectValue></SelectTrigger>
            <SelectContent position="popper"><SelectGroup>
              {groups.map(group => (
                <SelectItem key={group.segment.id} value={group.segment.id}>
                  {group.segment.name || group.segment.id} · {group.rows.length ? `${group.pending} 处待确认` : '待添加记录'}
                </SelectItem>
              ))}
            </SelectGroup></SelectContent>
          </Select>
        </div> : <div className="position-review-current-segment">
          <span className="text-sm text-muted-foreground">核对片段</span>
          <span className="text-sm font-medium">{currentGroup?.segment.name || currentGroup?.segment.id}</span>
        </div>}
        <UiButton type="button" onClick={onAdd} disabled={!canAdd || busy}>
          <Plus aria-hidden="true" data-icon="inline-start" />添加位置
        </UiButton>
      </div>
      {currentGroup?.rows.length ? (
        <div className="position-review-layout">
          <div className="position-review-list" aria-label="按镜头组织的人物位置记录">
            {currentGroup.shots.map((shot, index) => (
              <section className="position-review-shot" key={shot.id}>
                <h4 className="font-semibold leading-snug tracking-tight text-sm mb-0">
                  {shot.firstFrame ? `镜头 ${index + 1} · 第 ${shot.firstFrame}–${shot.lastFrame} 帧` : '待修正时间范围'}
                </h4>
                {shot.pending > 0 ? (
                  <div className="position-review-rows">{shot.rows.filter(row => row.observation.confirmed !== true).map(positionRow)}</div>
                ) : !shot.rows.length && <p className="text-sm text-muted-foreground position-review-note">暂无记录。</p>}
                {shot.confirmed > 0 && (
                  <PositionDisclosure label={`已确认 · ${shot.confirmed} 处`} compact disabled={busy}>
                    {shot.rows.filter(row => row.observation.confirmed === true).map(positionRow)}
                  </PositionDisclosure>
                )}
              </section>
            ))}
          </div>
          <div className="position-review-content" id={editorId}>
            {activeObservation && currentGroup.rows.some(row => row.index === activeIndex) ? (
              <ObservationEditor key={activeObservation.id || `${actor.id}:${activeIndex}`} value={activeObservation}
                onChange={value => onUpdateObservation(activeIndex, value)} project={project}
                actor={actor} index={activeIndex} disabled={busy} />
            ) : (
              <Empty className="position-review-empty p-4 md:p-4"><EmptyHeader><EmptyDescription>选择一条记录核对。</EmptyDescription></EmptyHeader></Empty>
            )}
          </div>
        </div>
      ) : (
        <Empty className="position-review-empty p-4 md:p-4"><EmptyHeader><EmptyDescription>
          暂无位置记录。
        </EmptyDescription></EmptyHeader></Empty>
      )}
    </div>
  );
}

function ObservationEditor({ value, onChange, project, actor, index, disabled }) {
  const field = useId();
  const video = useRef(null);
  const surface = useRef(null);
  const start = useRef(null);
  const [drawing, setDrawing] = useState(false);
  const selected = project.segments.find(segment => segment.id === value.segment_id);
  const label = `${actor.label}定位${index + 1}`;
  const { width = 16, height = 9, video_start_time } = project.source_meta || {};
  const videoOffset = Number(video_start_time || 0);
  const box = value.box || [.2, .1, .8, .9];
  const set = (key, next) => onChange({ ...value, [key]: next, confirmed: false });

  function seek(timestamp) {
    if (!video.current) return;
    video.current.currentTime = timestamp + videoOffset;
    video.current.pause();
  }

  useEffect(() => { seek(value.time); }, [value.time, videoOffset, project.source?.id]);
  useEffect(() => {
    if (!disabled) return;
    start.current = null;
    setDrawing(false);
    video.current?.pause();
  }, [disabled]);

  function point(event) {
    const rect = surface.current.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))];
  }

  function begin(event) {
    if (disabled || value.visibility === 'out') return;
    start.current = point(event);
    surface.current.setPointerCapture(event.pointerId);
  }

  function cancelDrawing() {
    start.current = null;
    setDrawing(false);
  }

  function finish(event) {
    if (disabled) { cancelDrawing(); return; }
    if (!start.current) return;
    const [endX, endY] = point(event);
    const [startX, startY] = start.current;
    if (Math.abs(endX - startX) > .01 && Math.abs(endY - startY) > .01) {
      const nextBox = [Math.min(startX, endX), Math.min(startY, endY), Math.max(startX, endX), Math.max(startY, endY)];
      set('box', nextBox.map(number => Number(number.toFixed(4))));
    }
    cancelDrawing();
  }

  return (
    <div className="position-review-editor">
      <div className="position-review-player-meta">
        <div className="position-review-field">
          <span className="text-sm text-muted-foreground">原片时间</span>
          <span className="text-xl font-medium position-review-time">{time(value.time)}</span>
        </div>
        <div className="position-review-field">
          <label htmlFor={`${field}-visibility`} className="text-sm font-medium">可见状态</label>
          <Select value={value.visibility || ''} onValueChange={next => set('visibility', next)} disabled={disabled}>
            <SelectTrigger id={`${field}-visibility`} aria-label={`${label}可见状态`}><SelectValue placeholder="请选择可见状态">{value.visibility === 'occluded' ? '被遮挡（仍需确认身份）' : visibilityLabels[value.visibility]}</SelectValue></SelectTrigger>
            <SelectContent position="popper"><SelectGroup>
              <SelectItem value="visible">可见</SelectItem>
              <SelectItem value="occluded">被遮挡（仍需确认身份）</SelectItem>
              <SelectItem value="out">已出画</SelectItem>
            </SelectGroup></SelectContent>
          </Select>
        </div>
      </div>
      <div className="position-review-video" style={{ aspectRatio: `${width || 16}/${height || 9}` }}>
        <video ref={video} src={assetUrl(project.source) || undefined} controls playsInline preload="metadata"
          onLoadedMetadata={() => seek(value.time)} aria-label={`${label}原片定位预览`} />
        <div className="position-review-box-surface" data-drawing={drawing && !disabled} ref={surface}
          onPointerDown={begin} onPointerUp={finish} onPointerCancel={cancelDrawing} aria-hidden="true">
          {value.visibility !== 'out' && (
            <span className="identity-box" style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`,
              width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%`, borderColor: actor.color }}>
              <span style={{ background: actor.color }}>{actor.label}</span>
            </span>
          )}
        </div>
      </div>
      <div className="position-review-player-actions">
        <UiButton type="button" disabled={disabled} onClick={() => set('time', Number(Math.max(0, (video.current?.currentTime || 0) - videoOffset).toFixed(4)))} variant={"outline"}>
          使用播放时间
        </UiButton>
        <UiButton type="button" aria-pressed={drawing} disabled={disabled || value.visibility === 'out'} onClick={() => { video.current?.pause(); setDrawing(previous => !previous); }} variant={drawing ? 'default' : 'outline'}>
          {drawing ? '取消画框' : '画框定位'}
        </UiButton>
      </div>
      <p className="text-sm text-muted-foreground position-review-note">修改后请重新确认，并保存人物。</p>
      <PositionDisclosure label="精确参数" title="调整所属片段、原片时间与坐标" disabled={disabled}>
        <div className="position-review-precision">
          <div className="position-review-field">
            <label htmlFor={`${field}-segment`} className="text-sm font-medium">所属片段</label>
            <Select value={selected?.id || ''} disabled={disabled || !project.segments.length} onValueChange={id => {
                const segment = project.segments.find(item => item.id === id);
                onChange({ ...value, segment_id: segment.id, time: segment.start, confirmed: false });
              }}>
              <SelectTrigger id={`${field}-segment`} aria-label={`${label}所在片段`}><SelectValue placeholder="请选择片段">{selected ? `${selected.name || selected.id} · ${time(selected.start)}—${time(selected.end)}` : undefined}</SelectValue></SelectTrigger>
              <SelectContent position="popper"><SelectGroup>
                {project.segments.map(segment => (
                  <SelectItem key={segment.id} value={segment.id}>
                    {segment.name || segment.id} · {time(segment.start)}—{time(segment.end)}
                  </SelectItem>
                ))}
              </SelectGroup></SelectContent>
            </Select>
          </div>
          <div className="position-review-field">
            <label htmlFor={`${field}-time`} className="text-sm font-medium">原片时间 / 秒</label>
            <Input id={`${field}-time`} type="number" min={selected?.start || 0} max={selected?.end} step="0.001" value={value.time} onChange={event => set('time', Number(event.target.value))} aria-label={`${label}原片时间秒`} disabled={disabled} />
          </div>
        </div>
        <p className="text-sm position-review-note">画面左上角为 0，右下角为 1。</p>
        <div className="position-review-coordinates">
          {coordinateLabels.map((name, coordinate) => (
            <div className="position-review-field" key={name}>
              <label htmlFor={`${field}-${coordinate}`} className="text-sm font-medium">{name}</label>
              <Input id={`${field}-${coordinate}`} type="number" min="0" max="1" step="0.001" value={box[coordinate]} aria-label={`${label}${name}`} disabled={disabled || value.visibility === 'out'} onChange={event => set('box', box.map((number, position) => position === coordinate ? Number(event.target.value) : number))} />
            </div>
          ))}
        </div>
      </PositionDisclosure>
      <label className="text-sm position-review-confirm">
        <Checkbox disabled={disabled} checked={value.confirmed === true} onCheckedChange={checked => onChange({ ...value, confirmed: checked === true })} />
        已对照原片确认时间、位置与可见状态
      </label>
    </div>
  );
}
