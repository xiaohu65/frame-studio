import {cn} from "@/lib/utils";
import React from 'react';

import { Button as UiButton } from './ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from './ui/card';
import { Separator } from './ui/separator';
import { Spinner } from './ui/spinner';

import { Download } from 'lucide-react';
import { assetUrl } from '../api';
import { ResultDisclosure, ResultNotice } from './ResultControls';
import { ResultFileDetails } from './ResultReceive';
import './ResultExport.css';

function FinalOutput({ project, current, subdued = false }) {
  const asset = project.final;
  const label = current ? '无字幕 MP4 母版' : '历史成片';

  return (
    <div className="result-final-output">
      <p className={cn("text-sm font-medium mb-0", current ? 'text-muted-foreground' : 'text-warning')}>
        {current ? '无字幕 MP4 母版已导出' : '历史成片 · 当前版本尚未核实'}
      </p>
      <video className="result-final-video" src={assetUrl(asset)} controls playsInline
        aria-label={current ? '正式无字幕母版' : label} />
      <div className="flex">
        <UiButton asChild variant={current && !subdued ? 'default' : 'outline'} size="default">
          <a href={assetUrl(asset)} download>
            <Download aria-hidden="true" data-icon="inline-start" />下载{current ? '无字幕母版' : '历史成片'}
          </a>
        </UiButton>
      </div>
      <ResultFileDetails assets={[[label, asset]]}>
        {project.assembly_report && <ResultDisclosure label="原始导出记录"><pre className="result-evidence-code">{JSON.stringify(project.assembly_report, null, 2)}</pre></ResultDisclosure>}
      </ResultFileDetails>
    </div>
  );
}

export default function ResultExport({
  project, whole, reviewOpen, reviewingExisting, locked, assembling, projectFailure, projectRunning,
  onExport, onSelectSegment, onExtractAudio,
}) {
  const audio = project.music?.asset;

  return (
    <>
      <Card className="result-export-panel" id="final-export" tabIndex={-1} aria-labelledby="result-export-title" role="region"><CardHeader><div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <CardTitle><h2 id="result-export-title" className="font-semibold leading-snug tracking-tight text-xl mb-0">导出完整成片</h2></CardTitle>
            </div>
            <CardDescription className="mb-0">全部片段验收后，拼接原始回片并接回连续原声，导出正式无字幕母版。</CardDescription>
          </div></CardHeader><CardContent>
        
          
          {project.final && (whole.finalCurrent
            ? <FinalOutput project={project} current subdued={reviewOpen} />
            : <ResultDisclosure label="历史成片">
              <FinalOutput project={project} current={false} />
            </ResultDisclosure>)}
          {project.final_master && <ResultFileDetails assets={[[ '历史成片母版', project.final_master ]]} />}
          {!whole.exportReady ? (
            <div className="flex flex-col gap-3">
              {whole.pending.length>0?<ResultDisclosure label={`待处理片段 · ${whole.pending.length} 段`}>
                <div className="flex flex-wrap gap-3">
                  {whole.pending.map(({ segment }) => (
                    <UiButton key={segment.id} type="button" onClick={() => onSelectSegment(segment.id)} variant="outline">
                      处理{segment.name || segment.id}
                    </UiButton>
                  ))}
                </div>
              </ResultDisclosure>:<p className="text-sm text-muted-foreground mb-0">补齐当前版本材料确认后可导出。</p>}
            </div>
          ) : whole.finalCurrent ? (
            <ResultDisclosure label="重新导出">
              <UiButton type="button" disabled={locked} onClick={onExport} data-testid="assemble-replacement-final" variant={"outline"} size="default">
                重新导出完整成片
              </UiButton>
            </ResultDisclosure>
          ) : (
            <div className="flex">
              <UiButton type="button" onClick={onExport} data-testid="assemble-replacement-final" variant={reviewingExisting ? 'outline' : 'default'} size="default" disabled={Boolean(assembling) || (locked)} aria-busy={(assembling) || undefined}>{(assembling) && <Spinner data-icon="inline-start" aria-hidden="true" />}
                导出完整成片
              </UiButton>
            </div>
          )}
          {projectFailure && (
            <ResultNotice tone="destructive" role="alert">{projectFailure.message || '本地导出未完成，请查看处理记录。'}</ResultNotice>
          )}
          {projectRunning && <p role="status" className="text-sm text-muted-foreground mb-0">{projectRunning.message || '正在本机导出'}</p>}
        
      <Separator decorative />
      <ResultDisclosure label="原声音轨">
        <div className="result-export-audio">
          {project.source_meta?.has_audio || project.source_meta?.audio ? (
            <>
              <div className="flex">
                <UiButton type="button" disabled={locked || !project.source} onClick={onExtractAudio} variant={"outline"} size="default">
                  提取独立原声
                </UiButton>
              </div>
              {audio && (
                <>
                  <audio controls src={assetUrl(audio)} aria-label="原片连续声音试听" />
                  <ResultFileDetails assets={[[ '原声音轨', audio ]]} />
                </>
              )}
            </>
          ) : <p className="text-sm text-muted-foreground mb-0">原片无音轨，成片保持无声。</p>}
        </div>
      </ResultDisclosure>
      </CardContent></Card>
    </>
  );
}
