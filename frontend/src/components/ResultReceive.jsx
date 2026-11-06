import React from 'react';

import { Card, CardContent, CardHeader, CardTitle } from './ui/card';
import { Badge } from './ui/badge';
import { Button as UiButton } from './ui/button';
import { Spinner } from './ui/spinner';
import { AlertDialog, AlertDialogTrigger, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogCancel, AlertDialogAction } from './ui/alert-dialog';

import { assetUrl } from '../api';
import { generationUsageRows } from '../generationPresentation';
import { resultSource } from '../resultPresentation';
import { specSeconds } from './DeliverySpec';
import { ResultDisclosure, ResultNotice } from './ResultControls';
import SceneFileImport from './SceneFileImport';
import './ResultReceive.css';

const CANDIDATE_FILES_ID = 'candidate-files';

function openCandidateFiles() {
  const files = document.getElementById(CANDIDATE_FILES_ID);
  const trigger = files?.querySelector(':scope > button[aria-expanded]');
  if (!trigger) return;
  if (trigger.getAttribute('aria-expanded') === 'false') trigger.click();
  files.scrollIntoView({ block: 'start', behavior: 'instant' });
  trigger.focus({ preventScroll: true });
}

export function ResultFileDetails({ assets, children, id }) {
  return (
    <ResultDisclosure id={id} label="文件详情" className="result-file-details" contentClassName="result-file-stack">
      {assets.filter(([, asset]) => asset).map(([label, asset]) => (
        <section className="result-file" key={label} aria-label={label}>
          <span className="text-sm font-bold">{label}</span>
          <a href={assetUrl(asset)} download className="text-primary underline-offset-4 hover:underline text-sm text-muted-foreground underline">
            {asset.name || label}
          </a>
          {asset.meta && <ResultDisclosure label="文件参数" contentClassName="result-file-stack">
            <pre className="result-evidence-code">{JSON.stringify(asset.meta, null, 2)}</pre>
          </ResultDisclosure>}
        </section>
      ))}
      {children}
    </ResultDisclosure>
  );
}

export function ResultReceipt({ segment, state, evidence, locked, onStep, onUpload, onImport }) {
  const historyAssets = [
    ['历史原始候选', segment?.raw_candidate],
    ['历史合成预览', segment?.composite_preview],
    ['历史合成母版', segment?.composited],
    ['历史小样预览', segment?.sample_preview],
  ];
  const hasDetails = state.hasResult || historyAssets.some(([, asset]) => asset) || segment?.candidate_review;
  const importControls = (
    <SceneFileImport
      label={state.hasResult ? '导入新的候选' : '导入当前段结果'}
      kindLabel={`${segment?.id || ''}段候选视频`}
      pathLabel="或填写本机文件路径"
      uploadVariant={state.hasResult ? 'outline' : 'default'}
      size="default"
      disabled={!segment || locked}
      accept="video/mp4,video/quicktime,video/x-matroska"
      onFiles={files => onUpload('generated', files, { segment_id: segment.id })}
      onImport={path => onImport({ path, kind: 'generated', segment_id: segment.id })}
    />
  );

  return (
    <Card className="result-receive result-receive-card" id="candidate-import" tabIndex={-1} aria-labelledby="candidate-import-title" role="region"><CardHeader><div className="flex items-center justify-between gap-3">
          <CardTitle><h2 id="candidate-import-title" className="font-semibold leading-snug tracking-tight text-xl">{state.hasResult ? '当前段结果' : '接收当前段结果'}</h2></CardTitle>
          <Badge variant={state.reviewed ? 'success' : 'secondary'}>{state.label}</Badge>
        </div></CardHeader><CardContent>
      
        
        {state.hasResult ? <>
          <p className="text-sm text-muted-foreground">{resultSource(segment)}</p>
          {!state.candidateCurrent && (
            <ResultNotice tone="warning">
              <p className="text-sm">{state.reasons?.candidate || '请接入当前段的原始候选。'}</p>
              <UiButton type="button" onClick={() => onStep?.(3)} variant={"outline"} size="default">返回生成视频</UiButton>
            </ResultNotice>
          )}
          <ResultDisclosure label="更换候选" contentClassName="result-file-stack">
            <p className="text-sm text-muted-foreground">更换后需重新检查和验收。</p>
            {importControls}
          </ResultDisclosure>
        </> : <>
          <p className="text-sm text-muted-foreground">平台生成的视频请导入这一段，API 结果会自动接入。</p>
          {importControls}
        </>}
        {hasDetails && (
          <ResultFileDetails id={CANDIDATE_FILES_ID}
            assets={[[state.candidateCurrent ? '原始候选' : '历史结果', segment?.generated], ...historyAssets]}>
            {segment?.generation_provenance && <ResultDisclosure label="原始接收记录" contentClassName="result-file-stack">
              <pre className="result-evidence-code">{JSON.stringify(segment.generation_provenance, null, 2)}</pre>
            </ResultDisclosure>}
            {segment?.candidate_review && !state.reviewed && <ResultDisclosure label="历史验收记录" contentClassName="result-file-stack">
              <pre className="result-evidence-code">{JSON.stringify(segment.candidate_review, null, 2)}</pre>
            </ResultDisclosure>}
          </ResultFileDetails>
        )}
      
    </CardContent></Card>
  );
}

function CandidateMetrics({ evidence }) {
  const { actual, expected, delta, coverage } = evidence;
  const usage = generationUsageRows(evidence.usage);
  const deltaLabel = delta === null ? '' : delta === 0 ? '与要求一致' : delta < 0 ? `少 ${-delta} 帧` : `多 ${delta} 帧`;
  const missingRanges = coverage?.missing_ranges || [];
  return (
    <div className="result-evidence-grid">
      {actual ? (
        <dl className="result-evidence-metrics result-evidence-summary">
          <div><dt>实测画面</dt><dd>{actual.width ?? '—'} × {actual.height ?? '—'} · {actual.fps_rational ?? '—'} 帧/秒</dd></div>
          <div><dt>回片 / 要求</dt><dd>{actual.frame_count ?? '待检查'} / {expected ?? '待检查'} 帧{deltaLabel && ` · ${deltaLabel}`}</dd></div>
          <div><dt>解码覆盖</dt><dd>{specSeconds(actual.decoded_duration_seconds)}</dd></div>
        </dl>
      ) : <p className="text-sm text-muted-foreground">待检查原始回片的解码帧数和时间戳。</p>}
      {missingRanges.length > 0 ? (
        <ResultNotice tone="warning">
          <p className="text-sm">回片缺少 {coverage.missing_frame_count} 个源帧时刻{coverage.estimated ? '（按首轮已测恒帧率位置推算）' : ''}。</p>
          <ul className="result-evidence-list">
            {missingRanges.map(gap => {
              const single = gap.source_first_frame === gap.source_last_frame;
              return <li key={gap.source_first_frame}>{single ? `原片 F${gap.source_first_frame}` : `原片 F${gap.source_first_frame}–F${gap.source_last_frame}`} · {specSeconds(gap.source_first_seconds)}{!single && `–${specSeconds(gap.source_last_seconds)}`}</li>;
            })}
          </ul>
        </ResultNotice>
      ) : delta < 0 && <p className="text-sm text-warning">已发现帧数不足，缺口的源帧位置待逐帧核对。</p>}
      <ResultDisclosure label="解码详情与平台回执" contentClassName="result-evidence-details">
        {actual && <dl className="result-evidence-metrics">
          <div><dt>首 / 末帧时间戳</dt><dd>{actual.first_pts_rational ?? '待检查'} / {actual.last_pts_rational ?? '待检查'} 秒</dd></div>
        </dl>}
        <section className="result-evidence-panel">
          <h3 className="font-semibold leading-snug tracking-tight text-base mb-0">平台计费用量</h3>
          {usage.length > 0 ? <>
            <dl className="result-evidence-metrics">{usage.map(row => <div key={row.key}><dt>{row.label}</dt><dd>{row.seconds.toFixed(3)} 秒</dd></div>)}</dl>
            <p className="text-sm text-muted-foreground mb-0">实际计费以官方账单为准。技术验收使用文件解码证据。</p>
          </> : <p className="text-sm text-muted-foreground mb-0">暂无与这份回片匹配的平台用量回执。</p>}
        </section>
      </ResultDisclosure>
    </div>
  );
}

export function ResultTechnicalCheck({ segment, state, evidence, request, running, lastFailure, locked, onCheck, onReview }) {
  const checking = running?.action === 'validate_candidate' || request === 'validate_candidate';
  const issues = evidence.report?.issues ?? evidence.precheckIssues;

  return (
    <Card className="result-processing result-receive-card" id="candidate-check" tabIndex={-1} aria-labelledby="candidate-check-title" role="region"><CardHeader><CardTitle><h2 id="candidate-check-title" className="font-semibold leading-snug tracking-tight text-xl">检查原始候选</h2></CardTitle>
      <span className="text-sm text-muted-foreground">技术检查 · <strong>{state.validationCurrent ? '通过' : evidence.report?.ready === false ? '未通过' : '待检查'}</strong></span>
    </CardHeader><CardContent>
      
        
        {checking ? (
          <ResultNotice role="status">
            <div className="flex items-center gap-2">
              <Spinner />{running?.status === 'QUEUED' ? '等待本机检查' : running?.message || '正在检查当前段'}
            </div>
          </ResultNotice>
        ) : state.validationCurrent ? (
          <div className="flex items-center gap-3 flex-wrap">
            <UiButton type="button" onClick={onReview} variant={"outline"} size="default">查看对照画面</UiButton>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-muted-foreground">{state.reasons?.validation || '检查候选的实际规格与时间覆盖。'}</p>
            <UiButton type="button" disabled={locked} onClick={onCheck} data-testid="validate-candidate" size="default">检查当前段</UiButton>
          </div>
        )}
        {lastFailure && !request && !running && (
          <ResultNotice tone="destructive" role="alert">
            <p className="text-sm">{lastFailure.message || '当前段处理未完成，请核对原始候选文件。'}</p>
            <UiButton type="button" onClick={openCandidateFiles} variant={"outline"} size="default">查看候选文件</UiButton>
          </ResultNotice>
        )}
        <CandidateMetrics evidence={evidence} />
        {issues?.length > 0 && (
          <ResultNotice tone={issues.some(issue => issue.blocking) ? 'destructive' : 'warning'}>
            <span className="text-sm font-bold">{evidence.report ? '技术检查记录' : '接收时的文件预检记录'}</span>
            <ul className="result-evidence-list">
              {issues.map((issue, index) => <li key={issue.code || index}>{issue.message}</li>)}
            </ul>
          </ResultNotice>
        )}
        {(segment.candidate_validation || state.validationCurrent) && (
          <ResultDisclosure label={state.validationCurrent ? '检查报告与重新检查' : '检查报告'} contentClassName="result-file-stack">
            {segment.candidate_validation && <ResultDisclosure label="原始检查记录" contentClassName="result-file-stack">
              <pre className="result-evidence-code">{JSON.stringify(segment.candidate_validation, null, 2)}</pre>
            </ResultDisclosure>}
            {state.validationCurrent && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <UiButton type="button" disabled={locked} data-testid="validate-candidate" variant={"outline"} size="default">重新检查原始候选</UiButton>
                </AlertDialogTrigger>
                <AlertDialogContent className="result-recheck-dialog sm:max-w-[520px]">
                  <AlertDialogTitle>重新检查原始候选</AlertDialogTitle>
                  <AlertDialogDescription>重新检查通过后需重新逐项验收。</AlertDialogDescription>
                  <div className="flex justify-end gap-3">
                    <AlertDialogCancel asChild><UiButton type="button" variant={"outline"} size="default">取消</UiButton></AlertDialogCancel>
                    <AlertDialogAction asChild><UiButton type="button" disabled={locked} onClick={onCheck} size="default">确认重新检查</UiButton></AlertDialogAction>
                  </div>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </ResultDisclosure>
        )}
      
    </CardContent></Card>
  );
}
